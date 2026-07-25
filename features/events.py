"""Events Management Feature (assignment commands).

Admins can assign/unassign users to events.

Commands:
    !assign <event_id> | @user
    !unassign <event_id> | @user
    !help events
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.event_store import EventStore
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import ContextInfo, ExtendedTextMessage, Message

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)


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


def _digits(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


def _resolve_mention(args: str, message: "MessageEv") -> tuple[str, str]:
    """Return (user_id, display_name); prefers a real phone JID over typed text.

    WhatsApp issues two JID formats in mentionedJid:
    - ``919876543210@s.whatsapp.net`` → real phone number (use the user part)
    - ``12345678901234567@lid``        → internal LID (skip; fall back to args)
    """
    try:
        ctx = message.Message.extendedTextMessage.contextInfo
        jid = ctx.mentionedJid[0] if ctx and ctx.mentionedJid else ""
    except (AttributeError, IndexError):
        jid = ""

    if jid:
        user_part, _, domain = jid.rpartition("@")
        if domain in ("s.whatsapp.net", ""):
            digits = _digits(user_part or jid)
            if digits:
                return digits, f"@{digits}"
        # @lid domains are internal identifiers, not phone numbers — fall through

    digits = _digits(args)
    stripped = args.strip()
    return digits, stripped if stripped.startswith("@") else (f"@{digits}" if digits else stripped)


def _require_admin(client: "NewClient", chat_jid, sender_user: str, store: EventStore) -> bool:
    if not store.is_admin(_digits(sender_user)):
        _reply(client, chat_jid, "⛔ Permission denied. Admin access required.")
        return False
    return True


def _cmd_assign_or_unassign(
    client: "NewClient", chat_jid, args: str, sender_user: str,
    store: EventStore, message: "MessageEv", *, assigning: bool,
) -> None:
    """!assign <event_id> | @user   /   !unassign <event_id> | @user"""
    label = "assign" if assigning else "unassign"
    if not _require_admin(client, chat_jid, sender_user, store):
        return

    parts = [p.strip() for p in args.split("|")]
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, f"⚠️ Usage: `!{label} <event_id> | @user`")
        return

    event_id = int(parts[0])
    target_user_id, display_name = _resolve_mention(parts[1], message)
    if not target_user_id:
        _reply(client, chat_jid, "❌ Could not determine target user from mention or ID.")
        return

    try:
        if assigning:
            a = store.assign(event_id=event_id, user_id=target_user_id)
            _reply(client, chat_jid, f"✅ {display_name} assigned to Event {event_id}. Status: `{a['status']}`")
        elif store.unassign(event_id=event_id, user_id=target_user_id):
            _reply(client, chat_jid, f"✅ {display_name} has been unassigned from Event {event_id}.")
        else:
            _reply(client, chat_jid, f"⚠️ {display_name} is not currently assigned to Event {event_id}.")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ *Error:* {exc}")
    except Exception:
        log.exception("Failed to %s user", label)
        _reply(client, chat_jid, f"❌ Failed to process {label}ment.")


def _cmd_help(client: "NewClient", chat_jid) -> None:
    """!help events"""
    _reply(client, chat_jid, (
        "*📋 Events Management Commands*\n\n"
        "*Member Commands:*\n"
        "• `!events` — List all active events\n"
        "• `!my` — Show your own assigned events\n"
        "• `!my-status <event_id> | <status>` — Update your status for an event\n"
        "  _Example:_ `!my-status 1 | completed`\n"
        "  _Valid statuses:_ pending, in_progress, completed, cancelled\n\n"
        "*Admin Commands:*\n"
        "• `!create-event <type> | <name> | [description]` — Create a new event\n"
        "  _Example:_ `!create-event participation | Hackathon | Annual coding contest`\n"
        "  _Types:_ participation, organization\n"
        "• `!assign <event_id> | @user` — Assign a user to an event\n"
        "  _Example:_ `!assign 1 | @919876543210`\n"
        "• `!unassign <event_id> | @user` — Unassign a user from an event\n"
        "  _Example:_ `!unassign 1 | @919876543210`\n"
        "• `!delete-event <event_id>` — Delete an event\n"
        "  _Example:_ `!delete-event 1`\n"
        "• `!set-status <event_id> | <status>` — Update an event's status\n"
        "  _Example:_ `!set-status 1 | active`\n\n"
        "ℹ️ *General:* `!help events` — Show this help message"
    ))


def _is_event_command(text: str) -> bool:
    lower = text.lower()
    return any(lower == cmd or lower.startswith(f"{cmd} ") for cmd in ("!assign", "!unassign", "!help events"))


def register(client: "NewClient", config: dict) -> callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Events feature requires db_session_factory")

    store = EventStore(session_factory)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat
        sender_user = getattr(message.Info.MessageSource.Sender, "User", "")

        body = _get_text(message)
        if not body:
            return

        if message.Info.MessageSource.IsFromMe and not _is_event_command(body):
            return

        lower = body.lower()

        if lower.startswith("!assign "):
            args = body[len("!assign"):].strip()
            _cmd_assign_or_unassign(client, chat, args, sender_user, store, message, assigning=True)
            return

        if lower.startswith("!unassign "):
            args = body[len("!unassign"):].strip()
            _cmd_assign_or_unassign(client, chat, args, sender_user, store, message, assigning=False)
            return

        if lower == "!help events":
            _cmd_help(client, chat)
            return

    log.info("✅ Events feature registered")
    return on_message