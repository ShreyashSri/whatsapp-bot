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
from db.auth import normalize_jid, require_member

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
    """Extract plain text body from a message."""
    msg = message.Message
    if msg.conversation:
        return msg.conversation.strip()
    if msg.extendedTextMessage and msg.extendedTextMessage.text:
        return msg.extendedTextMessage.text.strip()
    if msg.imageMessage and msg.imageMessage.caption:
        return msg.imageMessage.caption.strip()
    return ""

def _reply(client: "NewClient", chat_jid, text: str) -> None:
    """Helper to send a text reply to a specific chat."""
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

def _cmd_create_event(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """!create-event <type> | <name> | [description]"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 2:
        _reply(client, chat_jid, "⚠️ Usage: `!create-event <participation|organization> | <Name> | [Description]`")
        return

    ev_type = parts[0].lower()
    name = parts[1]
    desc = parts[2] if len(parts) > 2 else ""

    if ev_type not in ["participation", "organization"]:
        _reply(client, chat_jid, "❌ Event type must be `participation` or `organization`.")
        return

    try:
        event = store.create_event(
            name=name,
            type=ev_type,
            description=desc,
            status="active"
        )
        _reply(client, chat_jid, f"✅ Event *{name}* created successfully! (ID: {event['id']})")
    except Exception as exc:
        log.error("Failed to create event: %s", exc)
        _reply(client, chat_jid, f"❌ Failed to create event: {exc}")

def _cmd_delete_event(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """!delete-event <event_id>"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return

    match = re.match(r"^(\d+)$", args)
    if not match:
        _reply(client, chat_jid, "⚠️ Usage: `!delete-event <event_id>`")
        return

    event_id = int(match.group(1))
    try:
        success = store.delete_event(event_id)
        if success:
            _reply(client, chat_jid, f"🗑️ Event {event_id} has been deleted.")
        else:
            _reply(client, chat_jid, f"⚠️ Event {event_id} not found or already deleted.")
    except Exception as exc:
        log.error("Failed to delete event: %s", exc)
        _reply(client, chat_jid, "❌ Failed to delete event.")

def _cmd_set_status(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """!set-status <event_id> | <status>"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, "⚠️ Usage: `!set-status <event_id> | <status>`")
        return

    event_id = int(parts[0])
    status = parts[1].lower()

    try:
        updated_event = store.set_status(event_id=event_id, status=status)
        _reply(client, chat_jid, f"✅ Event {event_id} status changed to `{updated_event['status']}`.")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ {exc}")
    except Exception as exc:
        log.error("Failed to update status: %s", exc)
        _reply(client, chat_jid, "❌ Failed to update event status.")

def _cmd_my(client: "NewClient", chat_jid, sender_user: str, store: EventStore) -> None:
    """!my - Shows the member their own assignments"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    try:
        assignments = store.get_user_assignments(user_id=clean_sender_id)
        if not assignments:
            _reply(client, chat_jid, "📋 *You have no active event assignments right now.*")
            return

        lines = [
            f"• *[{asg['event_id']}]* {asg['event_name']} _({asg['event_type']})_ - Status: `{asg['status']}`"
            for asg in assignments
        ]
        _reply(client, chat_jid, f"*📌 Your Assigned Events ({len(assignments)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to fetch user assignments: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch your assignments.")

def _cmd_my_status(client: "NewClient", chat_jid, sender_user: str, args: str, store: EventStore) -> None:
    """!my-status <event_id> | <status> - Updates your status for an event"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    try:
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2 or not parts[0].isdigit():
            _reply(client, chat_jid, "⚠️ Usage: `!my-status <event_id> | <status>`\nExample: `!my-status 1 | completed`")
            return

        event_id = int(parts[0])
        new_status = parts[1].lower()

        if new_status not in VALID_ASSIGNMENT_STATUSES:
            allowed = " • ".join(sorted(VALID_ASSIGNMENT_STATUSES))
            _reply(client, chat_jid, f"❌ Invalid status `{new_status}`.\nAllowed values: {allowed}")
            return

        success = store.update_user_assignment_status(clean_sender_id, event_id, new_status)
        if success:
            _reply(client, chat_jid, f"✅ Your assignment status for Event {event_id} has been updated to `{new_status}`!")
        else:
            _reply(client, chat_jid, f"❌ You are not assigned to Event {event_id}.")
    except Exception as exc:
        log.error("Failed to update user assignment status: %s", exc)
        _reply(client, chat_jid, "❌ Failed to update your assignment status.")

def _is_event_command(text: str) -> bool:
    """Return whether text is an explicit event management command."""
    lower = text.lower()
    return any(
        lower == cmd or lower.startswith(f"{cmd} ")
        for cmd in ("!events", "!create-event", "!delete-event", "!set-status", "!my", "!my-status")
    )

# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Event management feature requires db_session_factory")

    store = EventStore(session_factory)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat
        sender = message.Info.MessageSource.Sender
        sender_user = getattr(sender, "User", "")

        body = _get_text(message)
        if not body:
            return

        if message.Info.MessageSource.IsFromMe and not _is_event_command(body):
            return

        lower = body.lower()

        if lower == "!events":
            if not require_member(session_factory, normalize_jid(message.Info.MessageSource.Sender), "events.list"):
                _reply(client, chat, "⛔ An active user account is required."); return
            _cmd_events(client, chat, store)
            return

        if lower.startswith("!create-event "):
            args = body[len("!create-event"):].strip()
            _cmd_create_event(client, chat, args, sender_user, store)
            return

        if lower.startswith("!delete-event "):
            args = body[len("!delete-event"):].strip()
            _cmd_delete_event(client, chat, args, sender_user, store)
            return

        if lower.startswith("!set-status "):
            args = body[len("!set-status"):].strip()
            _cmd_set_status(client, chat, args, sender_user, store)
            return

        if lower == "!my":
            if not require_member(session_factory, normalize_jid(message.Info.MessageSource.Sender), "events.my"):
                _reply(client, chat, "⛔ An active user account is required."); return
            _cmd_my(client, chat, sender_user, store)
            return

        if lower.startswith("!my-status "):
            if not require_member(session_factory, normalize_jid(message.Info.MessageSource.Sender), "events.my_status"):
                _reply(client, chat, "⛔ An active user account is required."); return
            args = body[len("!my-status"):].strip()
            _cmd_my_status(client, chat, sender_user, args, store)
            return

    log.info("✅ Event management feature registered")
    return on_message
