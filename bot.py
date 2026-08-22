#!/usr/bin/env python3
"""WhatsApp bot entrypoint.

The process owns configuration, database setup, legacy migration, Neonize
initialisation, feature registration, dispatch, and the connection lifecycle.
Feature business rules remain in the feature modules.
"""

from __future__ import annotations

import contextvars
import logging
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, MessageEv, PairStatusEv
from sqlalchemy.exc import OperationalError

from db.database import create_database
from db.auth import normalize_group_jid, normalize_jid
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
        "reminder_group_id": os.getenv("REMINDER_GROUP_ID", "").strip() or None,
        "incident_group_id": os.getenv("INCIDENT_GROUP_ID", "").strip() or None,
        "incident_port": int(os.getenv("INCIDENT_PORT", "8081")),
        "fellowship_alert_group_id": (
            os.getenv("FELLOWSHIP_ALERT_GROUP_ID", "").strip()
            or os.getenv("PBBOT_GROUP_ID", "").strip()
            or os.getenv("GROUP_ID", "").strip()
            or None
        ),
        "fellowship_alert_secret": os.getenv("FELLOWSHIP_ALERT_SECRET", "").strip(),
        "fellowship_alert_port": int(os.getenv("FELLOWSHIP_ALERT_PORT", "8082")),
        "subgroup_blocked_users": _parse_group_ids("SUBGROUP_BLOCKED_USERS"),
        "database_url": os.getenv("DATABASE_URL", "").strip(),
        "mistral_api_key": os.getenv("MISTRAL_API_KEY", "").strip(),
        "mistral_model": os.getenv("MISTRAL_MODEL", "mistral-small-latest").strip(),
        "mistral_card_model": os.getenv("MISTRAL_CARD_MODEL", "mistral-medium-3-5").strip(),
        "gemini_api_key": os.getenv("GEMINI_API_KEY", "").strip(),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemma-4-31b-it").strip(),
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


def _configured_groups(config: dict, *keys: str) -> set[str]:
    """Return normalized group JIDs from config keys and GROUP_IDS."""
    groups = set()
    for value in config.get("group_ids", set()) or set():
        normalized = normalize_group_jid(value)
        if normalized.endswith("@g.us"):
            groups.add(normalized)
    for key in keys:
        normalized = normalize_group_jid(config.get(key))
        if normalized.endswith("@g.us"):
            groups.add(normalized)
    return groups


def _allowed_inbound_chat(config: dict, chat, *, reminder_reply: bool = False) -> bool:
    """Allow any group the bot has joined; DMs only for tracked reminder replies.

    Group-specific restriction (media/cards to MEDIA_GROUP_ID, reminders to
    REMINDER_GROUP_ID) is enforced per-feature instead of at this global
    gate, so other modules stay usable from any group the bot is in.
    """
    chat_id = normalize_jid(_jid_string(chat))
    if chat_id.endswith("@g.us"):
        return True
    return reminder_reply and chat_id.endswith(("@s.whatsapp.net", "@lid"))


def _allowed_outbound_groups(config: dict) -> set[str]:
    """Return every configured group the bot may deliver to."""
    return _configured_groups(
        config,
        "pbbot_group_id",
        "media_group_id",
        "reminder_group_id",
        "incident_group_id",
        "fellowship_alert_group_id",
    )


def _connect_with_retry(
    client,
    *,
    retry_delay: float = 5.0,
    max_retry_delay: float = 60.0,
    max_attempts: int | None = None,
    sleep=time.sleep,
):
    """Keep transient WhatsApp connection failures from terminating the bot."""
    delay = max(0.1, float(retry_delay))
    max_delay = max(delay, float(max_retry_delay))
    attempt = 0
    while True:
        attempt += 1
        try:
            return client.connect()
        except KeyboardInterrupt:
            raise
        except Exception:
            if max_attempts is not None and attempt >= max_attempts:
                raise
            log.exception(
                "WhatsApp connection attempt %d failed; retrying in %.1fs",
                attempt,
                delay,
            )
            sleep(delay)
            delay = min(max_delay, delay * 2)


def _initialize_database_with_retry(
    database,
    *,
    data_dir: Path,
    retry_delay: float = 5.0,
    max_retry_delay: float = 60.0,
    max_attempts: int | None = None,
    sleep=time.sleep,
):
    """Keep transient PostgreSQL startup failures from terminating the bot."""
    from db.work_store import load_persistent_aliases

    delay = max(0.1, float(retry_delay))
    max_delay = max(delay, float(max_retry_delay))
    attempt = 0
    while True:
        attempt += 1
        try:
            database.initialize()
            upgrade_unified_schema(database)
            migrate_legacy_json(database.session_factory, data_dir)
            migrate_unified_work(database.session_factory)
            load_persistent_aliases(database.session_factory)
            return
        except KeyboardInterrupt:
            raise
        except OperationalError:
            if max_attempts is not None and attempt >= max_attempts:
                raise
            log.exception(
                "Database initialization attempt %d failed; retrying in %.1fs",
                attempt,
                delay,
            )
            sleep(delay)
            delay = min(max_delay, delay * 2)


def _set_readiness(path: Path, ready: bool) -> None:
    """Expose WhatsApp connection state to the container readiness probe."""
    try:
        if ready:
            path.touch()
        else:
            path.unlink(missing_ok=True)
    except OSError:
        log.exception("Could not update readiness marker %s", path)


# Kept available for callers that import bot configuration, without creating a
# database connection or a Neonize session as an import side effect.
config = _build_config()


def _excepthook(exc_type, exc_value, exc_tb):
    log.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _excepthook


def main() -> None:
    """Initialise all runtime dependencies and connect Neonize."""
    database = create_database(config["database_url"])
    _initialize_database_with_retry(database, data_dir=Path.cwd())
    readiness_path = Path("/tmp/pbbot-ready")
    _set_readiness(readiness_path, False)

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
    allowed_outbound_groups = _allowed_outbound_groups(runtime_config)

    # Enforce destination policy at the live client boundary. This protects
    # replies, media, moderation, group settings, and webhook deliveries.
    from features.neonize_policy import (
        allow_reminder_reply,
        allow_reply_to_source_chat,
        install_outbound_policy,
        is_reminder_reply,
    )
    install_outbound_policy(client, allowed_outbound_groups)

    # WhatsApp may replay pending/history messages immediately after a
    # reconnect. Fail closed during startup and ignore anything timestamped
    # before this process started, so old commands cannot run.
    startup_timestamp = int(time.time())
    accept_messages_after = time.monotonic() + 10

    @client.event(PairStatusEv)
    def on_pair_status(_client: NewClient, event: PairStatusEv):
        log.info("📱 Pair status: %s", event)

    def _start_reminder_scheduler(client: NewClient, session_factory, runtime_config: dict) -> None:
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
                        from features.reminders import configured_reminder_group
                        res = store.run_reminders(
                            client,
                            system_user,
                            force_ignore_window=False,
                            source="system",
                            group_jid=configured_reminder_group(runtime_config),
                        )
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
        _set_readiness(readiness_path, True)
        try:
            from neonize.utils import Jid2String
            runtime_config["bot_jid"] = Jid2String(client.get_me().JID)
        except Exception as e:
            log.warning("Could not determine bot JID: %s", e)
        _start_reminder_scheduler(client, runtime_config["db_session_factory"], runtime_config)
        _reconcile_lid_assignments(client, runtime_config["db_session_factory"])

    @client.event(DisconnectedEv)
    def on_disconnected(_client: NewClient, event: DisconnectedEv):
        _set_readiness(readiness_path, False)
        log.warning("WhatsApp connection lost: %s", event)

    def _reconcile_lid_assignments(client: NewClient, session_factory) -> None:
        """Resolve LID-based assignments to phone JIDs using the live client.

        Always resolves known LIDs and populates the in-memory alias registry
        (_JID_ALIASES) so that overview() can match assignments regardless of
        whether the sender arrives as a LID or phone JID.
        """
        import threading

        def _run():
            import time as _time
            _time.sleep(5)  # let connection fully settle
            try:
                from db.work_store import WorkStore
                from neonize.utils import build_jid, Jid2String
                from db.auth import normalize_jid
                from db.models import Assignment, User
                from sqlalchemy import select

                store = WorkStore(session_factory)

                # Collect all unique user JIDs currently in assignments (any form).
                with session_factory() as session:
                    all_assignment_jids = list(dict.fromkeys(
                        row.user_jid
                        for row in session.scalars(select(Assignment)).all()
                        if row.user_jid
                    ))
                    # Also collect any user JIDs from the users table that look like LIDs.
                    all_user_jids = list(dict.fromkeys(
                        u.jid
                        for u in session.scalars(select(User)).all()
                        if u.jid
                    ))

                # Build a candidate set of LIDs to resolve.
                # Include both LID-form JIDs from assignments AND try to get LIDs
                # for phone-form JIDs (reverse lookup) so we cover both directions.
                lid_to_phone: dict[str, str] = {}

                # Forward: LID -> phone
                candidate_lids = [j for j in (all_assignment_jids + all_user_jids)
                                  if j.endswith("@lid")]
                for lid in dict.fromkeys(candidate_lids):
                    try:
                        u, s = lid.split("@", 1)
                        lid_jid = build_jid(u, s)
                        pn_jid = client.get_pn_from_lid(lid_jid)
                        if pn_jid:
                            phone = normalize_jid(Jid2String(pn_jid))
                            if phone and phone.endswith("@s.whatsapp.net"):
                                lid_to_phone[lid] = phone
                                log.info("📋 LID resolved: %s -> %s", lid, phone)
                    except Exception as exc:
                        log.warning("Could not resolve LID %s: %s", lid, exc)

                # Reverse: phone -> LID  (covers the case where DB is already
                # fully migrated and has zero @lid rows — we still need the
                # alias so that incoming LID mentions resolve correctly).
                candidate_phones = [j for j in (all_assignment_jids + all_user_jids)
                                    if j.endswith("@s.whatsapp.net")]
                for phone in dict.fromkeys(candidate_phones):
                    if phone in lid_to_phone.values():
                        continue  # already covered by forward lookup
                    try:
                        u, s = phone.split("@", 1)
                        pn_jid = build_jid(u, s)
                        lid_jid = client.get_lid_from_pn(pn_jid)
                        if lid_jid:
                            lid = normalize_jid(Jid2String(lid_jid))
                            if lid and lid.endswith("@lid"):
                                lid_to_phone[lid] = phone
                                log.info("📋 Reverse LID resolved: %s -> %s", lid, phone)
                    except Exception as exc:
                        log.warning("Could not reverse-resolve phone %s: %s", phone, exc)

                if lid_to_phone:
                    migrated = store.reconcile_all_lids(lid_to_phone)
                    log.info("📋 LID reconciliation complete: %d row(s) migrated, "
                             "%d alias(es) registered.", migrated, len(lid_to_phone))

                else:
                    log.info("📋 No LID->phone mappings resolved; alias registry empty.")
            except Exception as exc:
                log.exception("Error during LID assignment reconciliation: %s", exc)

        thread = threading.Thread(target=_run, name="LIDReconciler", daemon=True)
        thread.start()


    dispatch = register_features(client, runtime_config)

    # Neonize invokes MessageEv callbacks synchronously. A single bounded
    # worker preserves message order while keeping slow natural-language/API
    # work out of the transport callback.
    dispatch_queue: queue.Queue = queue.Queue(maxsize=64)

    # Neonize client calls (get_me, send_message, get_group_info, ...) are
    # blocking network I/O with no built-in timeout. A WhatsApp connection
    # can go silently half-dead -- socket still open, no error, just no
    # response -- for hours before whatsmeow itself notices and reconnects
    # (observed in prod: ~8h). Since dispatch() runs on a single worker to
    # preserve message order, one such call permanently wedges every future
    # message with no crash and no log line. Running each dispatch() through
    # a bounded pool with a hard timeout means a stuck call can no longer
    # take the whole bot down with it -- worst case we leak one blocked
    # worker thread and keep going.
    DISPATCH_TIMEOUT_SECONDS = 60
    dispatch_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="MessageDispatchWorker")

    def _dispatch_worker() -> None:
        while True:
            queued_message = dispatch_queue.get()
            try:
                queued_info = getattr(queued_message, "Info", None)
                queued_source = getattr(queued_info, "MessageSource", None)
                queued_chat = getattr(queued_source, "Chat", None)
                queued_id = getattr(queued_info, "ID", "")
                queued_chat_jid = _jid_string(queued_chat)
                from db.nl_state import claim_message, release_message
                if not claim_message(
                    runtime_config["db_session_factory"],
                    queued_id,
                    getattr(queued_source, "Sender", ""),
                    queued_chat_jid,
                ):
                    continue
                try:
                    with allow_reminder_reply(queued_message), allow_reply_to_source_chat(queued_message):
                        # ThreadPoolExecutor does not propagate contextvars to
                        # its worker thread on its own -- the allow_* context
                        # managers above set ContextVars in *this* thread, so
                        # without an explicit copy_context().run() dispatch()
                        # would see none of that authorization once it starts
                        # running on the executor's own thread.
                        ctx = contextvars.copy_context()
                        future = dispatch_executor.submit(ctx.run, dispatch, queued_message)
                        try:
                            future.result(timeout=DISPATCH_TIMEOUT_SECONDS)
                        except FutureTimeoutError:
                            log.error(
                                "message dispatch timed out after %ss (id=%s chat=%s) -- "
                                "likely a stuck Neonize call on a half-dead connection; "
                                "abandoning this attempt and continuing",
                                DISPATCH_TIMEOUT_SECONDS, queued_id, queued_chat_jid,
                            )
                            release_message(runtime_config["db_session_factory"], queued_id, queued_chat_jid)
                            continue
                except BaseException:
                    # dispatch() is expected to catch its own user-facing
                    # errors (bad command, validation failure) and reply
                    # instead of raising. Anything that still escapes here is
                    # an unexpected failure (DB hiccup, bug) rather than a
                    # message the bot legitimately handled -- release the
                    # claim so the same message ID can be retried instead of
                    # being silently dropped forever.
                    release_message(runtime_config["db_session_factory"], queued_id, queued_chat_jid)
                    raise
            except BaseException:
                # This is the only consumer of dispatch_queue. Catching only
                # Exception would let a stray BaseException (e.g. surfaced
                # from a C extension) kill this thread silently -- every
                # message sent afterward would queue up and eventually be
                # dropped once the bounded queue fills, with no supervisor
                # to restart it. A daemon worker thread never receives
                # KeyboardInterrupt directly, so catching broadly here is safe.
                log.exception("message dispatch worker failed")
            finally:
                dispatch_queue.task_done()

    threading.Thread(
        target=_dispatch_worker,
        name="MessageDispatch",
        daemon=True,
    ).start()
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
        if not info:
            return
        
        # Dynamically register incoming LID senders to the alias registry
        # so overview() can find their phone-based assignments.
        source = getattr(info, "MessageSource", None)
        if source is None:
            return
        if getattr(source, "IsFromMe", False):
            return

        chat = getattr(source, "Chat", None)
        if not _allowed_inbound_chat(
            runtime_config,
            chat,
            reminder_reply=is_reminder_reply(message),
        ):
            return

        sender_str = getattr(source, "Sender", None)
        if sender_str:
            from db.auth import normalize_jid as _nj, jid_user as _ju
            sender = _nj(sender_str)
            if sender.endswith("@lid"):
                from db.work_store import _JID_ALIASES
                if _ju(sender) not in _JID_ALIASES:
                    def resolve_in_bg():
                        try:
                            import time
                            time.sleep(1)
                            from neonize.utils import build_jid, Jid2String
                            user, server = sender.split("@")
                            lid_jid = build_jid(user, server)
                            pn_jid = _client.get_pn_from_lid(lid_jid)
                            if pn_jid:
                                phone = _nj(Jid2String(pn_jid))
                                if phone and phone.endswith("@s.whatsapp.net"):
                                    _JID_ALIASES[_ju(sender)] = _ju(phone)
                                    log.info("📋 Dynamically registered LID alias in bg: %s -> %s", sender, phone)
                                    from db.work_store import save_persistent_alias
                                    save_persistent_alias(runtime_config["db_session_factory"], _ju(sender), _ju(phone))
                        except Exception as e:
                            log.warning("Failed background LID resolve for %s: %s", sender, e)
                    
                    import threading
                    threading.Thread(target=resolve_in_bg, daemon=True).start()

        if time.monotonic() < accept_messages_after:
            return

        message_timestamp = int(getattr(info, "Timestamp", 0) or 0)
        if message_timestamp <= startup_timestamp:
            return

        try:
            dispatch_queue.put_nowait(message)
            log.info(
                "accepted inbound WhatsApp message id=%s sender=%s chat=%s from_me=%s",
                getattr(info, "ID", ""),
                _jid_string(sender_str),
                _jid_string(chat),
                bool(getattr(source, "IsFromMe", False)),
            )
        except queue.Full:
            log.warning("message dispatch queue is full; dropping message %s", getattr(info, "ID", ""))
            try:
                client.send_message(chat, "⚠️ The bot is busy. Please send that command again in a moment.")
            except Exception:
                log.exception("could not report a full dispatch queue")

    log.info("Starting WhatsApp bot...")
    log.info("Groups: %s", config["group_ids"] or "(none)")
    log.info("Media group: %s", config["media_group_id"] or "(not set)")
    log.info("Incident group: %s", config["incident_group_id"] or "(not set)")
    log.info("Fellowship alert group: %s",config["fellowship_alert_group_id"] or "(not set)",)
    try:
        retry_delay = float(os.getenv("WHATSAPP_CONNECT_RETRY_SECONDS", "5"))
    except ValueError:
        retry_delay = 5.0
    _connect_with_retry(client, retry_delay=retry_delay)


if __name__ == "__main__":
    main()
