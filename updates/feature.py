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
    get_update_history,
    get_assignment_status,
    set_assignment_status,
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
    """!update <assignment_id_or_name> <field> <value>"""
    parts = args.split(" ", 2)
    if len(parts) < 3:
        _reply(client, chat_jid, "Usage: `!update <assignment_id_or_name> <field> <value>`")
        return

    id_or_name = parts[0]
    field = parts[1]
    value = parts[2]

    with session_factory() as session:
        try:
            submit_update(session, id_or_name, field, value)
            _reply(client, chat_jid, f"Update submitted for assignment *{id_or_name}*.")
        except ValueError as e:
            _reply(client, chat_jid, f"{e}")
        except Exception as e:
            log.error("submit_update error: %s", e)
            _reply(client, chat_jid, "An error occurred while submitting the update.")


def _cmd_history(client, chat_jid, args: str, session_factory) -> None:
    """!history <assignment_id_or_name>"""
    if not args:
        _reply(client, chat_jid, "Usage: `!history <assignment_id_or_name>`")
        return

    id_or_name = args.strip()

    with session_factory() as session:
        try:
            history = get_update_history(session, id_or_name)
            if not history:
                _reply(client, chat_jid, f"No updates found for assignment *{id_or_name}*.")
            else:
                lines = [f"*Update history for Assignment {id_or_name}*\n"]
                for up in history:
                    dt = up.timestamp.strftime("%Y-%m-%d %H:%M")
                    lines.append(f"[#{up.id}] [{dt}] *{up.field}*: {up.value}")
                _reply(client, chat_jid, "\n".join(lines))
        except ValueError as e:
            _reply(client, chat_jid, f"{e}")
        except Exception as e:
            log.error("get_update_history error: %s", e)
            _reply(client, chat_jid, "An error occurred while fetching history.")


def _cmd_status(client, chat_jid, args: str, session_factory) -> None:
    """!status <assignment_id_or_name>"""
    if not args:
        _reply(client, chat_jid, "Usage: `!status <assignment_id_or_name>`")
        return

    id_or_name = args.strip()

    with session_factory() as session:
        assignment = get_assignment_status(session, id_or_name)
        if not assignment:
            _reply(client, chat_jid, f"Assignment *{id_or_name}* not found.")
        else:
            _reply(
                client,
                chat_jid,
                f"*Status for Assignment {id_or_name}*\n"
                f"Status: {assignment.status}\n"
                f"Reminder State: {assignment.reminder_state}\n"
                f"Missed Reminders: {assignment.missed_count}",
            )


def _cmd_set_status(client, chat_jid, args: str, session_factory, admin_number: str, message_info) -> None:
    """!set-status <assignment> <status>"""
    sender = message_info.MessageSource.Sender
    sender_jid = getattr(sender, "User", "")
    is_from_me = getattr(message_info.MessageSource, "IsFromMe", False)

    # Log the variables to figure out why it's failing
    log.warning(f"DEBUG ADMIN CHECK: admin_number='{admin_number}', sender_jid='{sender_jid}', is_from_me={is_from_me}")

    # Check if the sender is the admin (or if it's the bot itself and the bot IS the admin number)
    if not admin_number or (not sender_jid.startswith(admin_number) and not is_from_me):
        _reply(client, chat_jid, f"Permission denied: Admin only. (Detected sender: {sender_jid})")
        return

    parts = args.split(" ", 1)
    if len(parts) < 2:
        _reply(client, chat_jid, "Usage: `!set-status <assignment> <status>`")
        return

    id_or_name = parts[0]
    new_status = parts[1]

    with session_factory() as session:
        try:
            set_assignment_status(session, id_or_name, new_status)
            _reply(client, chat_jid, f"Status for assignment *{id_or_name}* set to *{new_status}*.")
        except ValueError as e:
            _reply(client, chat_jid, f"{e}")
        except Exception as e:
            log.error("set_assignment_status error: %s", e)
            _reply(client, chat_jid, "An error occurred while updating the status.")


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

HELP_TEXT = """*Updates Module — Available Commands*

!update <assignment> <field> <value>
  Submit a progress update.
  Example: !update gsoc_manas proposal_link docs.google.com/abc

!history <assignment>
  View all updates submitted for an assignment.
  Example: !history gsoc_manas

!status <assignment>
  View the current status of an assignment.
  Example: !status hackathon_team_alpha

!help-update
  Show this help message.

*Admin Commands*

!set-status <assignment> <status>
  Change the status of an assignment.
  Example: !set-status gsoc_manas completed

Note: <assignment> can be a name (gsoc_manas) or a numeric ID (1)."""


def _cmd_help(client, chat_jid) -> None:
    """!help-update — show all update commands."""
    _reply(client, chat_jid, HELP_TEXT)


def register(client: "NewClient", config: dict) -> callable:
    """Register the updates feature on the neonize client."""
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Updates feature requires db_session_factory")
        
    admin_number = config.get("admin_number")

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        body = _get_text(message)
        if not body:
            return

        lower = body.lower()
        chat = message.Info.MessageSource.Chat

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
            
        if lower == "!set-status" or lower.startswith("!set-status "):
            args = body[len("!set-status"):].strip()
            _cmd_set_status(client, chat, args, session_factory, admin_number, message.Info)
            return

        if lower == "!help-update":
            _cmd_help(client, chat)
            return

    log.info("Updates feature registered")
    return on_message
