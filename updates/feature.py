"""WhatsApp commands for assignment progress updates.

Commands:
    !update <assignment_id> <field> <value>
    !history <assignment_id>
    !status <assignment_id>
    !set-status <assignment_id> <status>

The event-management feature owns ``!set-status <event_id> | <status>``;
the space-separated form here updates an individual assignment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.auth import gate, normalize_jid

from .operations import (
    get_assignment_status,
    get_update_history,
    set_assignment_status,
    submit_update,
)

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


def _is_update_command(text: str) -> bool:
    """Allow explicit update commands from the bot account during testing."""
    lower = text.lower()
    return any(
        lower == command or lower.startswith(f"{command} ")
        for command in ("!update", "!history", "!status", "!set-status", "!help-update")
    )


def _cmd_submit_update(client, chat_jid, args: str, session_factory, sender) -> None:
    parts = args.split(None, 2)
    if len(parts) < 3:
        _reply(client, chat_jid, "Usage: `!update <assignment_id> <field> <value>`")
        return
    if not gate(session_factory, sender, client, chat_jid, "member", "update.submit"):
        return

    try:
        with session_factory() as session:
            submit_update(session, parts[0], parts[1], parts[2])
        _reply(client, chat_jid, f"✅ Update submitted for assignment *{parts[0]}*.")
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to submit assignment update")
        _reply(client, chat_jid, "❌ An error occurred while submitting the update.")


def _cmd_history(client, chat_jid, args: str, session_factory, sender) -> None:
    if not args:
        _reply(client, chat_jid, "Usage: `!history <assignment_id>`")
        return
    if not gate(session_factory, sender, client, chat_jid, "member", "update.history"):
        return

    try:
        with session_factory() as session:
            history = get_update_history(session, args)
        if not history:
            _reply(client, chat_jid, f"No updates found for assignment *{args}*.")
            return
        lines = [f"*Update history for assignment {args}*", ""]
        for update in history:
            timestamp = update.timestamp.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"[#{update.id}] [{timestamp}] *{update.field}*: {update.value}")
        _reply(client, chat_jid, "\n".join(lines))
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to fetch assignment update history")
        _reply(client, chat_jid, "❌ An error occurred while fetching history.")


def _cmd_status(client, chat_jid, args: str, session_factory, sender) -> None:
    if not args:
        _reply(client, chat_jid, "Usage: `!status <assignment_id>`")
        return
    if not gate(session_factory, sender, client, chat_jid, "member", "update.status"):
        return

    try:
        with session_factory() as session:
            assignment = get_assignment_status(session, args)
        _reply(
            client,
            chat_jid,
            f"*Status for assignment {args}*\n"
            f"Status: `{assignment.status}`\n"
            f"Reminder state: `{assignment.reminder_state or 'inactive'}`\n"
            f"Missed reminders: `{assignment.missed_count}`",
        )
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to fetch assignment status")
        _reply(client, chat_jid, "❌ An error occurred while fetching status.")


def _cmd_set_status(client, chat_jid, args: str, session_factory, sender) -> None:
    parts = args.split()
    if len(parts) != 2:
        _reply(client, chat_jid, "Usage: `!set-status <assignment_id> <status>`")
        return
    if not gate(session_factory, sender, client, chat_jid, "admin", "update.set_status"):
        return

    try:
        with session_factory() as session:
            set_assignment_status(session, parts[0], parts[1])
        _reply(
            client,
            chat_jid,
            f"✅ Status for assignment *{parts[0]}* set to *{parts[1].lower()}*.",
        )
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to set assignment status")
        _reply(client, chat_jid, "❌ An error occurred while updating the status.")


HELP_TEXT = """*Updates Module — Available Commands*

`!update <assignment_id> <field> <value>`
  Submit or replace a progress update.

`!history <assignment_id>`
  View submitted updates.

`!status <assignment_id>`
  View assignment status.

`!set-status <assignment_id> <status>`
  Change an assignment status (admin only).
  Statuses: pending, in_progress, completed, cancelled.

`!help-update`
  Show this help message."""


def register(client: "NewClient", config: dict) -> callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Updates feature requires db_session_factory")

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        source = message.Info.MessageSource
        chat = source.Chat

        # Keep the same routing boundary and sender handling as subgroups:
        # update commands are group-only, and the complete JID (including
        # @lid identities) is normalized before authorization.
        if getattr(chat, "Server", "") != "g.us":
            return

        body = _get_text(message)
        if not body:
            return

        if source.IsFromMe and not _is_update_command(body):
            return

        sender = normalize_jid(source.Sender)

        lower = body.lower()
        if lower == "!help-update":
            _reply(client, chat, HELP_TEXT)
            return

        if lower == "!update" or lower.startswith("!update "):
            _cmd_submit_update(
                client, chat, body[len("!update"):].strip(), session_factory, sender
            )
            return

        if lower == "!history" or lower.startswith("!history "):
            _cmd_history(
                client, chat, body[len("!history"):].strip(), session_factory, sender
            )
            return

        if lower == "!status" or lower.startswith("!status "):
            _cmd_status(
                client, chat, body[len("!status"):].strip(), session_factory, sender
            )
            return

        if lower == "!set-status" or lower.startswith("!set-status "):
            args = body[len("!set-status"):].strip()
            # The event feature owns the pipe-delimited event-status form.
            if "|" not in args:
                _cmd_set_status(client, chat, args, session_factory, sender)

    log.info("✅ Updates feature registered")
    return on_message
