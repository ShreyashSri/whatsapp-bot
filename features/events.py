"""Events Management Feature.

Allows admins to create participation/organization events and users to assign 
themselves to these events.

Commands (based on PRD):
    /events
    /create-event <type> | <name> | [description]
    /assign <event_id> | @user
    /unassign <event_id> | @user
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

def _extract_target_user(args: str, message: "MessageEv") -> str:
    """Helper to extract user ID from either a mention tag or text argument."""
    target_user_id = ""
    # Try extracting from WhatsApp mention context info
    try:
        context_info = message.Message.extendedTextMessage.contextInfo
        if context_info and context_info.mentionedJid:
            target_user_id = "".join(c for c in context_info.mentionedJid[0] if c.isdigit())
    except (AttributeError, IndexError):
        pass

    # Fallback to manual text parsing if no mention was found
    if not target_user_id:
        target_user_id = "".join(c for c in args if c.isdigit())

    return target_user_id

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
            f"• *[{ev['id']}]* {ev['name']} _({ev['type']})_ [`{ev['status']}`] - 👥 {ev.get('assignment_count', 0)} assigned" 
            for ev in events
        ]
        _reply(client, chat_jid, f"*📋 Active Events ({len(events)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to list events: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch events.")

def _cmd_create_event(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """/create-event <type> | <name> | [description]"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return
    
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

def _cmd_assign(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore, message: "MessageEv") -> None:
    """/assign <event_id> | @user"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Only Admins can assign users.")
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, "⚠️ Usage: `/assign <event_id> | @user`")
        return

    event_id = int(parts[0])
    target_user_id = _extract_target_user(parts[1], message)
    
    # Extract a friendly display name (e.g., "Shivam" from "@Shivam" or fallback to the ID)
    display_name = parts[1] if "@" in parts[1] else f"User {target_user_id}"

    if not target_user_id:
        _reply(client, chat_jid, "❌ Could not determine target user from mention or ID.")
        return

    try:
        assignment = store.assign(event_id=event_id, user_id=target_user_id)
        _reply(client, chat_jid, f"✅ {display_name} assigned to Event {event_id}. Status: `{assignment['status']}`")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ *Error:* {exc}")
    except Exception as exc:
        log.error("Failed to assign user: %s", exc)
        _reply(client, chat_jid, "❌ Failed to process assignment.")


def _cmd_unassign(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore, message: "MessageEv") -> None:
    """/unassign <event_id> | @user"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Only Admins can unassign users.")
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, "⚠️ Usage: `/unassign <event_id> | @user`")
        return

    event_id = int(parts[0])
    target_user_id = _extract_target_user(parts[1], message)
    
    # Extract friendly display name
    display_name = parts[1] if parts[1].startswith("@") else f"User {target_user_id}"

    if not target_user_id:
        _reply(client, chat_jid, "❌ Could not determine target user from mention or ID.")
        return

    try:
        success = store.unassign(event_id=event_id, user_id=target_user_id)
        if success:
            _reply(client, chat_jid, f"✅ {display_name} has been unassigned from Event {event_id}.")
        else:
            _reply(client, chat_jid, f"⚠️ {display_name} is not currently assigned to Event {event_id}.")
    except Exception as exc:
        log.error("Failed to unassign user: %s", exc)
        _reply(client, chat_jid, "❌ Failed to process unassignment.")

def _cmd_delete_event(client: "NewClient", chat_jid, args: str, sender_user: str, store: EventStore) -> None:
    """/delete-event <event_id>"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return

    match = re.match(r"^(\d+)$", args)
    if not match:
        _reply(client, chat_jid, "⚠️ Usage: `/delete-event <event_id>`")
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
    """/set-status <event_id> | <status>"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    if not store.is_admin(clean_sender_id):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, "⚠️ Usage: `/set-status <event_id> | <status>`")
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
    """/my - Shows the member their own assignments"""
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
    """/my-status <event_id> | <status> - Updates your status for an event"""
    clean_sender_id = "".join(c for c in sender_user if c.isdigit())
    try:
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 2 or not parts[0].isdigit():
            _reply(client, chat_jid, "⚠️ Usage: `/my-status <event_id> | <status>`\nExample: `/my-status 1 | completed`")
            return

        event_id = int(parts[0])
        new_status = parts[1].lower()

        success = store.update_user_assignment_status(clean_sender_id, event_id, new_status)
        if success:
            _reply(client, chat_jid, f"✅ Your assignment status for Event {event_id} has been updated to `{new_status}`!")
        else:
            _reply(client, chat_jid, f"❌ You are not assigned to Event {event_id}.")
    except Exception as exc:
        log.error("Failed to update user assignment status: %s", exc)
        _reply(client, chat_jid, "❌ Failed to update your assignment status.")

def _cmd_help(client: "NewClient", chat_jid) -> None:
    """/help events"""
    help_text = (
        "*📋 Events Management Commands*\n\n"
        "`/events` — list all active events and their assignment counts\n"
        "`/create-event <type> | <name> | [description]` — create a new participation or organization event (Admin only)\n"
        "`/assign <event_id> | @user` — assign a user to an event (Admin only)\n"
        "`/unassign <event_id> | @user` — unassign a user from an event (Admin only)\n"
        "`/delete-event <event_id>` — delete an event (Admin only)\n"
        "`/set-status <event_id> | <status>` — update an event's status (Admin only)\n"
        "`/my` — show your own active event assignments\n"
        "`/my-status <event_id> | <status>` — update your personal assignment status for an event\n\n"
        "_Event types:_ participation • organization"
    )
    _reply(client, chat_jid, help_text)

def _is_event_command(text: str) -> bool:
    """Return whether text is an explicit event command."""
    lower = text.lower()
    return any(
        lower == cmd or lower.startswith(f"{cmd} ")
        for cmd in ("/events", "/create-event", "/assign", "/unassign", "/delete-event", "/set-status", "/my", "/my-status", "/help events")
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
        sender = message.Info.MessageSource.Sender
        sender_user = getattr(sender, "User", "")

        body = _get_text(message)
        if not body:
            return

        if message.Info.MessageSource.IsFromMe and not _is_event_command(body):
            return

        lower = body.lower()

        if lower == "/events":
            _cmd_events(client, chat, store)
            return

        if lower.startswith("/create-event "):
            args = body[len("/create-event"):].strip()
            _cmd_create_event(client, chat, args, sender_user, store) 
            return

        if lower.startswith("/assign "):
            args = body[len("/assign"):].strip()
            _cmd_assign(client, chat, args, sender_user, store, message)
            return

        if lower.startswith("/unassign "):
            args = body[len("/unassign"):].strip()
            _cmd_unassign(client, chat, args, sender_user, store, message)
            return

        if lower.startswith("/delete-event "):
            args = body[len("/delete-event"):].strip()
            _cmd_delete_event(client, chat, args, sender_user, store) 
            return

        if lower == "/help events" or lower == "!help events":
            _cmd_help(client, chat)
            return

        if lower.startswith("/set-status "):
            args = body[len("/set-status"):].strip()
            _cmd_set_status(client, chat, args, sender_user, store)
            return 

        if lower == "/my":
            _cmd_my(client, chat, sender_user, store)
            return

        if lower.startswith("/my-status "):
            args = body[len("/my-status"):].strip()
            _cmd_my_status(client, chat, sender_user, args, store)
            return

    log.info("✅ Events feature registered")
    return on_message