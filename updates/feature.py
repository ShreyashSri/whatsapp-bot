"""WhatsApp command interface for the Updates module.

Follows the same structural pattern as the existing features (subgroups, incidents).

Commands (Member):
    !update <assignment_id> <field> <value>   — submit.update (update.submit)
    !update-edit <update_id> <new_value>      — update.edit
    !history <assignment_id>                  — update.history
    !status <assignment_id>                   — view assignment status
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from updates.operations import (
    submit_update,
    edit_update,
    get_update_history,
    get_assignment_status,
)

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Reply helper
# ---------------------------------------------------------------------------

def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_submit_update(client, chat_jid, args: str, session_factory) -> None:
    """!update <assignment_id> <field> <value>"""
    parts = args.split(" ", 2)
    if len(parts) < 3:
        _reply(client, chat_jid, "Usage: `!update <assignment_id> <field> <value>`")
        return

    try:
        assignment_id = int(parts[0])
    except ValueError:
        _reply(client, chat_jid, "Assignment ID must be a number.")
        return

    field = parts[1]
    value = parts[2]

    with session_factory() as session:
        try:
            submit_update(session, assignment_id, field, value)
            _reply(client, chat_jid, f"Update submitted for assignment *{assignment_id}*.")
        except ValueError as e:
            _reply(client, chat_jid, f"{e}")
        except Exception as e:
            log.error("submit_update error: %s", e)
            _reply(client, chat_jid, "An error occurred while submitting the update.")


def _cmd_edit_update(client, chat_jid, args: str, session_factory) -> None:
    """!update-edit <update_id> <new_value>"""
    parts = args.split(" ", 1)
    if len(parts) < 2:
        _reply(client, chat_jid, "Usage: `!update-edit <update_id> <new_value>`")
        return

    try:
        update_id = int(parts[0])
    except ValueError:
        _reply(client, chat_jid, "Update ID must be a number.")
        return

    new_value = parts[1]

    with session_factory() as session:
        try:
            edit_update(session, update_id, new_value)
            _reply(client, chat_jid, f"Update *{update_id}* edited successfully.")
        except ValueError as e:
            _reply(client, chat_jid, f"{e}")
        except Exception as e:
            log.error("edit_update error: %s", e)
            _reply(client, chat_jid, "An error occurred while editing the update.")


def _cmd_history(client, chat_jid, args: str, session_factory) -> None:
    """!history <assignment_id>"""
    if not args:
        _reply(client, chat_jid, "Usage: `!history <assignment_id>`")
        return

    try:
        assignment_id = int(args)
    except ValueError:
        _reply(client, chat_jid, "Assignment ID must be a number.")
        return

    with session_factory() as session:
        try:
            history = get_update_history(session, assignment_id)
            if not history:
                _reply(client, chat_jid, f"No updates found for assignment *{assignment_id}*.")
            else:
                lines = [f"*Update history for Assignment {assignment_id}*\n"]
                for up in history:
                    dt = up.timestamp.strftime("%Y-%m-%d %H:%M")
                    lines.append(f"• [{dt}] *{up.field}*: {up.value}")
                _reply(client, chat_jid, "\n".join(lines))
        except ValueError as e:
            _reply(client, chat_jid, f"{e}")
        except Exception as e:
            log.error("get_update_history error: %s", e)
            _reply(client, chat_jid, "An error occurred while fetching history.")


def _cmd_status(client, chat_jid, args: str, session_factory) -> None:
    """!status <assignment_id>"""
    if not args:
        _reply(client, chat_jid, "Usage: `!status <assignment_id>`")
        return

    try:
        assignment_id = int(args)
    except ValueError:
        _reply(client, chat_jid, "Assignment ID must be a number.")
        return

    with session_factory() as session:
        assignment = get_assignment_status(session, assignment_id)
        if not assignment:
            _reply(client, chat_jid, f"Assignment *{assignment_id}* not found.")
        else:
            _reply(
                client,
                chat_jid,
                f"*Status for Assignment {assignment_id}*\n"
                f"• Status: {assignment.status}\n"
                f"• Reminder State: {assignment.reminder_state}\n"
                f"• Missed Reminders: {assignment.missed_count}",
            )


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    """Register the updates feature on the neonize client."""
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Updates feature requires db_session_factory")

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        body = _get_text(message)
        if not body:
            return

        lower = body.lower()
        chat = message.Info.MessageSource.Chat

        # !update-edit must be checked before !update (longer prefix first)
        if lower == "!update-edit" or lower.startswith("!update-edit "):
            args = body[len("!update-edit"):].strip()
            if not args:
                _reply(client, chat, "Usage: `!update-edit <update_id> <new_value>`")
            else:
                _cmd_edit_update(client, chat, args, session_factory)
            return

        if lower == "!update" or lower.startswith("!update "):
            args = body[len("!update"):].strip()
            if not args:
                _reply(client, chat, "Usage: `!update <assignment_id> <field> <value>`")
            else:
                _cmd_submit_update(client, chat, args, session_factory)
            return

        if lower == "!history" or lower.startswith("!history "):
            args = body[len("!history"):].strip()
            _cmd_history(client, chat, args, session_factory)
            return

        if lower == "!status" or lower.startswith("!status "):
            args = body[len("!status"):].strip()
            _cmd_status(client, chat, args, session_factory)
            return

    log.info("Updates feature registered")
    return on_message
