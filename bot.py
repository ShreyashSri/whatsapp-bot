#!/usr/bin/env python3
"""WhatsApp bot entrypoint.

The process owns configuration, database setup, legacy migration, Neonize
initialisation, feature registration, dispatch, and the connection lifecycle.
Feature business rules remain in the feature modules.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv

from db.database import create_database
from db.auth import normalize_jid
from db.migration import migrate_legacy_json, migrate_unified_work, upgrade_unified_schema
from features import fellowship_alerts
from features.registry import register_features

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

load_dotenv()


def _parse_group_ids(*env_keys: str) -> set[str]:
    """Parse comma-separated group IDs from one or more environment values."""
    ids: set[str] = set()
    for key in env_keys:
        for group_id in os.getenv(key, "").split(","):
            group_id = group_id.strip()
            if group_id:
                ids.add(group_id)
    return ids


def _build_config() -> dict:
    return {
        "group_ids": _parse_group_ids("GROUP_ID", "GROUP_IDS"),
        # The primary PBBot group is the only group allowed to trigger bot
        # handlers. Keep GROUP_ID as the backwards-compatible default.
        "pbbot_group_id": (
            os.getenv("PBBOT_GROUP_ID", "").strip()
            or os.getenv("GROUP_ID", "").strip()
            or None
        ),
        "media_group_id": os.getenv("MEDIA_GROUP_ID", "").strip() or None,
        "incident_group_id": os.getenv("INCIDENT_GROUP_ID", "").strip() or None,
        "incident_port": int(os.getenv("INCIDENT_PORT", "8081")),
        "fellowship_alert_group_id": (
            os.getenv("FELLOWSHIP_ALERT_GROUP_ID", "").strip()
            or pbbot_group_id
        ),
        "fellowship_alert_secret": os.getenv("FELLOWSHIP_ALERT_SECRET", "").strip(),
        "fellowship_alert_port": int(os.getenv("FELLOWSHIP_ALERT_PORT", "8082")),
        "subgroup_blocked_users": _parse_group_ids("SUBGROUP_BLOCKED_USERS"),
        "database_url": os.getenv("DATABASE_URL", "").strip(),
        "mistral_api_key": os.getenv("MISTRAL_API_KEY", "").strip(),
        "mistral_model": os.getenv("MISTRAL_MODEL", "mistral-small-latest").strip(),
        "mistral_card_model": os.getenv("MISTRAL_CARD_MODEL", "mistral-medium-3-5").strip(),
        "natural_language_knowledge_urls": os.getenv("NATURAL_LANGUAGE_KNOWLEDGE_URLS", "").strip(),
        "bot_jid": os.getenv("BOT_JID", "").strip(),
    }


def _jid_string(jid) -> str:
    """Return a canonical WhatsApp JID, matching list_groups.py output."""
    user = getattr(jid, "User", "")
    server = getattr(jid, "Server", "")
    if user and server:
        return f"{user}@{server}"
    return str(jid)


# Kept available for callers that import bot configuration, without creating a
# database connection or a Neonize session as an import side effect.
config = _build_config()


def _excepthook(exc_type, exc_value, exc_tb):
    log.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _excepthook


def main() -> None:
    """Initialise all runtime dependencies and connect Neonize."""
    database = create_database(config["database_url"])
    database.initialize()
    upgrade_unified_schema(database)
    migrate_legacy_json(database.session_factory, Path.cwd())
    migrate_unified_work(database.session_factory)

    runtime_config = {
        **config,
        "db_session_factory": database.session_factory,
    }

    # Keep neonize.db independent from PostgreSQL: it is the WhatsApp session
    # database and must remain persistent across container restarts.
    session_db = Path(os.getenv("NEONIZE_SESSION_DB", "neonize.db"))
    if not session_db.is_absolute():
        session_db = Path.cwd() / session_db
    client = NewClient(str(session_db))
    allowed_group = runtime_config.get("pbbot_group_id")

    # TEST-ONLY GUARD: remove this block when production routing is designed.
    allowed_outbound_groups = {
        group_id
        for group_id in (
            allowed_group,
            runtime_config.get("fellowship_alert_group_id"),
        )
        if group_id
    }

    # Outbound destination guard: allow messages only to configured bot groups.
    # Enforce the destination policy at the last possible point. This also
    # covers outbound messages originating from independent features such as
    # the incident webhook, not just replies to inbound messages.
    original_send_message = client.send_message

    def guarded_send_message(chat_jid, *args, **kwargs):
        if isinstance(chat_jid, str):
            chat_str = normalize_jid(chat_jid)
            if "@" in chat_str:
                u, s = chat_str.split("@", 1)
            else:
                u, s = chat_str, "s.whatsapp.net"
            try:
                from neonize.utils import build_jid
                chat_jid = build_jid(u, s)
            except Exception as e:
                log.warning("Failed to build JID for %s: %s", chat_jid, e)

        chat_id = _jid_string(chat_jid) if chat_jid else ""
        if (
            chat_id.endswith("@s.whatsapp.net")
            or chat_id.endswith("@lid")
            or (chat_id.endswith("@g.us") and chat_id in allowed_outbound_groups)
        ):
            return original_send_message(chat_jid, *args, **kwargs)
        log.warning("Blocked outbound message to non-PBBot chat: %s", chat_id or "(unknown)")
        return None

    client.send_message = guarded_send_message

    # WhatsApp may replay pending/history messages immediately after a
    # reconnect. Fail closed during startup and ignore anything timestamped
    # before this process started, so old commands cannot run.
    startup_timestamp = int(time.time())
    accept_messages_after = time.monotonic() + 10

    @client.event(PairStatusEv)
    def on_pair_status(_client: NewClient, event: PairStatusEv):
        log.info("📱 Pair status: %s", event)

    def _start_reminder_scheduler(client: NewClient, session_factory) -> None:
        import threading
        from db.auth import upsert_user, current_user
        from db.reminder_store import ReminderStore

        # ConnectedEv can fire again after a reconnect. Keep exactly one
        # scheduler thread attached to the bot process.
        if getattr(client, "_pbbot_reminder_scheduler_started", False):
            log.info("⏰ Reminder scheduler already running; skipping duplicate start.")
            return
        client._pbbot_reminder_scheduler_started = True

        def run_scheduler_loop():
            # Wait a bit for the system to settle down after connection
            time.sleep(30)
            try:
                upsert_user(session_factory, "system@s.whatsapp.net", role="admin")
            except Exception as e:
                log.warning("Could not upsert system user: %s", e)

            store = ReminderStore(session_factory)
            while True:
                try:
                    system_user = current_user(session_factory, "system@s.whatsapp.net")
                    if system_user:
                        log.info("⏰ Running background reminders check...")
                        res = store.run_reminders(client, system_user, force_ignore_window=False, source="system")
                        log.info("⏰ Background reminders run result: %s", res)
                except Exception as exc:
                    log.exception("Error in background reminder scheduler loop: %s", exc)
                time.sleep(900)

        thread = threading.Thread(target=run_scheduler_loop, name="ReminderScheduler", daemon=True)
        thread.start()
        log.info("⏰ Background reminder scheduler thread started.")

    @client.event(ConnectedEv)
    def on_connected(_client: NewClient, _event: ConnectedEv):
        log.info("✅ Bot connected to WhatsApp — all features active")
        _start_reminder_scheduler(client, runtime_config["db_session_factory"])

    dispatch = register_features(client, runtime_config)
    log.info(
        "Registering fellowship alerts before WhatsApp connect: group=%s port=%s secret_configured=%s",
        runtime_config.get("fellowship_alert_group_id") or "(not set)",
        runtime_config.get("fellowship_alert_port"),
        bool(runtime_config.get("fellowship_alert_secret")),
    )
    try:
        fellowship_alerts.register(client, runtime_config)
    except Exception:
        log.exception("Fellowship alert webhook registration failed")
        raise

    @client.event(MessageEv)
    def on_message(_client: NewClient, message: MessageEv):
        info = getattr(message, "Info", None)
        source = getattr(info, "MessageSource", None)
        chat = getattr(source, "Chat", None)
        chat_id = _jid_string(chat) if chat else ""
        is_from_me = getattr(info, "IsFromMe", False)

        if time.monotonic() < accept_messages_after:
            return

        message_timestamp = int(getattr(info, "Timestamp", 0) or 0)
        if message_timestamp <= startup_timestamp:
            return

        # TEST-ONLY GUARD: remove this exact-group check when production
        # routing is designed.
        if (
            not allowed_group
            or not chat_id.endswith("@g.us")
            or chat_id != allowed_group
        ):
            return
        dispatch(message)

    log.info("Starting WhatsApp bot...")
    log.info("Groups: %s", config["group_ids"] or "(none)")
    log.info("Media group: %s", config["media_group_id"] or "(not set)")
    log.info("Incident group: %s", config["incident_group_id"] or "(not set)")
    log.info("Fellowship alert group: %s",config["fellowship_alert_group_id"] or "(not set)",)
    client.connect()


if __name__ == "__main__":
    main()
