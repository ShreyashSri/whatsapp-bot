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
from db.migration import migrate_legacy_json
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
        "subgroup_blocked_users": _parse_group_ids("SUBGROUP_BLOCKED_USERS"),
        "database_url": os.getenv("DATABASE_URL", "").strip(),
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
    migrate_legacy_json(database.session_factory, Path.cwd())

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
    # Enforce the destination policy at the last possible point. This also
    # covers outbound messages originating from independent features such as
    # the incident webhook, not just replies to inbound messages.
    original_send_message = client.send_message

    def guarded_send_message(chat_jid, *args, **kwargs):
        chat_id = _jid_string(chat_jid) if chat_jid else ""
        if (
            not allowed_group
            or not chat_id.endswith("@g.us")
            or chat_id != allowed_group
        ):
            log.warning("Blocked outbound message to non-PBBot chat: %s", chat_id or "(unknown)")
            return None
        return original_send_message(chat_jid, *args, **kwargs)

    client.send_message = guarded_send_message

    # WhatsApp may replay pending/history messages immediately after a
    # reconnect. Fail closed during startup and ignore anything timestamped
    # before this process started, so old commands cannot run.
    startup_timestamp = int(time.time())
    accept_messages_after = time.monotonic() + 10

    @client.event(PairStatusEv)
    def on_pair_status(_client: NewClient, event: PairStatusEv):
        log.info("📱 Pair status: %s", event)

    @client.event(ConnectedEv)
    def on_connected(_client: NewClient, _event: ConnectedEv):
        log.info("✅ Bot connected to WhatsApp — all features active")

    dispatch = register_features(client, runtime_config)

    @client.event(MessageEv)
    def on_message(_client: NewClient, message: MessageEv):
        if time.monotonic() < accept_messages_after:
            return

        info = getattr(message, "Info", None)
        message_timestamp = int(getattr(info, "Timestamp", 0) or 0)
        if message_timestamp <= startup_timestamp:
            return

        # TEST-ONLY GUARD: remove this exact-group check when production
        # routing is designed.
        source = getattr(info, "MessageSource", None)
        chat = getattr(source, "Chat", None)
        chat_id = _jid_string(chat) if chat else ""
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
    client.connect()


if __name__ == "__main__":
    main()
