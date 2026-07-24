"""Events Management Feature.

Allows admins to create participation/organization events and users to assign 
themselves to these events.

Commands (based on PRD):
  /events
  /create-event <type> | <name> | [description]
  /assign <event_id>
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from db.event_store import EventStore

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Message text extraction (Matching subgroups.py)
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
    """/events"""
    try:
        events = store.list_events(include_deleted=False)
        if not events:
            _reply(client, chat_jid, "📅 *No active events right now.*")
            return

        lines = [
        f"• *[{ev['id']}]* {ev['name']} _({ev['type']})_ - 👥 {ev.get('assignment_count', 0)} assigned" 
        for ev in events
    ]
        _reply(client, chat_jid, f"*📋 Active Events ({len(events)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to list events: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch events.")

def _cmd_create_event(client: "NewClient", chat_jid, args: str, store: EventStore) -> None:
    """/create-event <type> | <name> | [description]"""
    parts = [p.strip() for p in args.split("|")]
    
    if len(parts) < 2:
        _reply(client, chat_jid, "⚠️ Usage: `/create-event <participation|organization> | <Name> | [Description]`")
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

def _cmd_assign(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """/assign <event_id>"""
    match = re.match(r"^(\d+)$", args)
    if not match:
        _reply(client, chat_jid, "⚠️ Usage: `/assign <event_id>`")
        return

    event_id = int(match.group(1))
    
    
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())

    try:
        assignment = store.assign(event_id=event_id, user_id=clean_sender_id)
        _reply(client, chat_jid, f"✅ You are assigned to Event {event_id}. Status: `{assignment['status']}`")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ *Error:* {exc}")
    except Exception as exc:
        log.error("Failed to assign user: %s", exc)
        _reply(client, chat_jid, "❌ Failed to process assignment.")

def _cmd_unassign(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """/unassign <event_id>"""
    match = re.match(r"^(\d+)$", args)
    if not match:
        _reply(client, chat_jid, "⚠️ Usage: `/unassign <event_id>`")
        return

    event_id = int(match.group(1))
    
    # Keep it as a string to prevent the Integer crash!
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())

    try:
        # Assuming your store.unassign returns a boolean indicating success
        success = store.unassign(event_id=event_id, user_id=clean_sender_id)
        if success:
            _reply(client, chat_jid, f"✅ You have been unassigned from Event {event_id}.")
        else:
            _reply(client, chat_jid, f"⚠️ You are not currently assigned to Event {event_id}.")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ *Error:* {exc}")
    except Exception as exc:
        log.error("Failed to unassign user: %s", exc)
        _reply(client, chat_jid, "❌ Failed to process unassignment.")

def _is_event_command(text: str) -> bool:
    """Return whether text is an explicit event command."""
    lower = text.lower()
    return any(
        lower == cmd or lower.startswith(f"{cmd} ")
        for cmd in ("/events", "/create-event", "/assign", "/unassign")
    )

# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Events feature requires db_session_factory")
        
    store = EventStore(session_factory)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat
        
        # Only process group messages (like subgroups) or allow direct messages?
        # Typically bot commands work in both. We'll allow both for now, but 
        # you can enforce "g.us" if needed by uncommenting the next two lines:
        # if getattr(chat, "Server", "") != "g.us":
        #     return

        sender = message.Info.MessageSource.Sender
        sender_user = getattr(sender, "User", "")

        body = _get_text(message)
        if not body:
            return

        # Prevent recursive loops from bot's own replies
        if message.Info.MessageSource.IsFromMe and not _is_event_command(body):
            return

        # ----- Command handling -----
        lower = body.lower()

        if lower == "/events":
            _cmd_events(client, chat, store)
            return

        if lower.startswith("/create-event "):
            # TODO: RBAC check - verify if sender_user is in config.get("admin_users")
            args = body[len("/create-event"):].strip()
            _cmd_create_event(client, chat, args, store)
            return

        if lower.startswith("/assign "):
            args = body[len("/assign"):].strip()
            _cmd_assign(client, chat, args, sender_user, store)
            return

        if lower.startswith("/unassign "):
            args = body[len("/unassign"):].strip()
            _cmd_unassign(client, chat, args, sender_user, store)
            return
    log.info("✅ Events feature registered")
    return on_message