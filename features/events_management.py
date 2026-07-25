"""Event Management Feature (Core Actions).

Allows admins to create and manage events, and members to view and update
their personal assignment statuses.

Commands:
    !events
    !create-event <type> | <name> | [description]
    !delete-event <event_id>
    !set-status <event_id> | <status>
    !my
    !my-status <event_id> | <status>
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from db.event_store import EventStore
from db.auth import gate, normalize_jid
from db.work_store import WorkStore

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

# Allowed values a member can set via !my-status
VALID_ASSIGNMENT_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "completed", "cancelled"})


# ---------------------------------------------------------------------------
# Message text extraction
# ---------------------------------------------------------------------------

def _get_text(message: "MessageEv") -> str:
    msg = message.Message
    if msg.conversation:
        return msg.conversation.strip()
    if msg.extendedTextMessage and msg.extendedTextMessage.text:
        return msg.extendedTextMessage.text.strip()
    if msg.imageMessage and msg.imageMessage.caption:
        return msg.imageMessage.caption.strip()
    return ""


def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_events(client: "NewClient", chat_jid, store: EventStore) -> None:
    """!events"""
    try:
        events = store.list_events(include_deleted=False)
        if not events:
            _reply(client, chat_jid, "📅 *No active events right now.*")
            return
        lines = [
            f"• *[{ev['id']}]* {ev['name']} _({ev['type']})_ [`{ev['status']}`] - 👥 {ev.get('assignment_count', 0)} assigned"
            for ev in events
        ]
        _reply(client, chat_jid, f"*📋 Active Events ({len(events)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to list events: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch events.")


def _cmd_create_event(client: "NewClient", chat_jid, args: str, store: EventStore) -> None:
    """!create-event <type> | <name> | [description]"""
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 2:
        _reply(client, chat_jid, "⚠️ Usage: `!create-event <participation|organization> | <Name> | [Description]`")
        return

    ev_type = parts[0].lower()
    name = parts[1]
    desc = parts[2] if len(parts) > 2 else ""

    if ev_type not in ("participation", "organization"):
        _reply(client, chat_jid, "❌ Event type must be `participation` or `organization`.")
        return

    try:
        event = store.create_event(name=name, type=ev_type, description=desc, status="active")
        _reply(client, chat_jid, f"✅ Event *{name}* created successfully! (ID: {event['id']})")
    except Exception as exc:
        log.error("Failed to create event: %s", exc)
        _reply(client, chat_jid, f"❌ Failed to create event: {exc}")


def _cmd_delete_event(client: "NewClient", chat_jid, args: str, store: EventStore) -> None:
    """!delete-event <event_id>"""
    match = re.match(r"^(\d+)$", args)
    if not match:
        _reply(client, chat_jid, "⚠️ Usage: `!delete-event <event_id>`")
        return
    event_id = int(match.group(1))
    try:
        if store.delete_event(event_id):
            _reply(client, chat_jid, f"🗑️ Event {event_id} has been deleted.")
        else:
            _reply(client, chat_jid, f"⚠️ Event {event_id} not found or already deleted.")
    except Exception as exc:
        log.error("Failed to delete event: %s", exc)
        _reply(client, chat_jid, "❌ Failed to delete event.")


def _cmd_set_status(client: "NewClient", chat_jid, args: str, store: EventStore) -> None:
    """!set-status <event_id> | <status>"""
    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, "⚠️ Usage: `!set-status <event_id> | <status>`")
        return
    try:
        updated = store.set_status(event_id=int(parts[0]), status=parts[1].lower())
        _reply(client, chat_jid, f"✅ Event {parts[0]} status changed to `{updated['status']}`.")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ {exc}")
    except Exception as exc:
        log.error("Failed to update status: %s", exc)
        _reply(client, chat_jid, "❌ Failed to update event status.")


def _cmd_my(client: "NewClient", chat_jid, sender_jid: str, store: EventStore) -> None:
    """!my — show own assignments"""
    try:
        assignments = store.get_user_assignments(user_id=sender_jid)
        if not assignments:
            _reply(client, chat_jid, "📋 *You have no active event assignments right now.*")
            return
        lines = [
            f"• *[{a['event_id']}]* {a['event_name']} _({a['event_type']})_ - Status: `{a['status']}`"
            for a in assignments
        ]
        _reply(client, chat_jid, f"*📌 Your Assigned Events ({len(assignments)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to fetch user assignments: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch your assignments.")


def _cmd_my_status(client: "NewClient", chat_jid, sender_jid: str, args: str, store: EventStore) -> None:
    """!my-status <event_id> | <status>"""
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 2 or not parts[0].isdigit():
        _reply(client, chat_jid,
               "⚠️ Usage: `!my-status <event_id> | <status>`\nExample: `!my-status 1 | completed`")
        return

    new_status = parts[1].lower()
    if new_status not in VALID_ASSIGNMENT_STATUSES:
        _reply(client, chat_jid,
               f"❌ Invalid status `{new_status}`.\nAllowed: {' • '.join(sorted(VALID_ASSIGNMENT_STATUSES))}")
        return

    try:
        try:
            WorkStore(store.session_factory).set_status(f"event:{int(parts[0])}@{sender_jid}", new_status, sender_jid)
            changed = True
        except ValueError:
            changed = False
        if changed:
            _reply(client, chat_jid,
                   f"✅ Your assignment status for Event {parts[0]} updated to `{new_status}`!")
        else:
            _reply(client, chat_jid, f"❌ You are not assigned to Event {parts[0]}.")
    except Exception as exc:
        log.error("Failed to update assignment status: %s", exc)
        _reply(client, chat_jid, "❌ Failed to update your assignment status.")


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

EVENT_MGMT_CMDS = ("!events", "!create-event", "!delete-event", "!set-status", "!my", "!my-status")


def _is_event_mgmt_command(text: str) -> bool:
    lower = text.lower()
    return any(lower == cmd or lower.startswith(f"{cmd} ") for cmd in EVENT_MGMT_CMDS)


def register(client: "NewClient", config: dict) -> callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Event management feature requires db_session_factory")

    store = EventStore(session_factory)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        source = message.Info.MessageSource
        chat = source.Chat

        if getattr(chat, "Server", "") != "g.us":
            return

        body = _get_text(message)
        if not body or not _is_event_mgmt_command(body):
            return

        lower = body.lower()
        command, _, args = body.partition(" ")
        cmd = command.lower()
        sender_jid = normalize_jid(source.Sender)

        # --- member-accessible commands (auto-provisions new users) ---
        if cmd in ("!events", "!my", "!my-status"):
            if not gate(session_factory, source.Sender, client, chat, "member", f"events.{cmd[1:]}"):
                return
            if cmd == "!events":
                _cmd_events(client, chat, store)
            elif cmd == "!my":
                _cmd_my(client, chat, sender_jid, store)
            elif cmd == "!my-status":
                _cmd_my_status(client, chat, sender_jid, args, store)
            return

        # --- admin-only commands ---
        # !set-status with no pipe is used by the updates feature — skip it here
        if cmd == "!set-status" and "|" not in args:
            return

        if not gate(session_factory, source.Sender, client, chat, "admin", f"events.{cmd[1:]}"):
            return

        if cmd == "!create-event":
            _cmd_create_event(client, chat, args, store)
        elif cmd == "!delete-event":
            _cmd_delete_event(client, chat, args, store)
        elif cmd == "!set-status":
            _cmd_set_status(client, chat, args, store)

    log.info("✅ Event management feature registered")
    return on_message
