"""WhatsApp commands for assignment progress updates.

Commands:
    !update <target> <field> <value>
    !update-edit <update_id> <new_value>
    !history <target>
    !status <target>
    !set-status <target> <status>

The event-management feature owns ``!set-status <event_id> | <status>``;
the space-separated form here updates an individual assignment.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.auth import gate, normalize_jid

from .operations import (
    edit_update,
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
        for command in (
            "!update",
            "!update-edit",
            "!history",
            "!status",
            "!set-status",
            "!help-update",
        )
    )


def _parse_target(args: str) -> tuple[str, list[str]]:
    """Parse ``event 4@jid`` / ``task 7@jid`` without colon syntax.

    The old ``event:4@jid`` and ``task:7@jid`` forms are still accepted by
    the storage resolver for compatibility.
    """
    tokens = args.split()
    if not tokens:
        return "", []
    if tokens[0].lower() in ("event", "task"):
        if len(tokens) < 2:
            return "", []
        target = f"{tokens[0]} {tokens[1]}"
        consumed = 2
        if consumed < len(tokens) and tokens[2].startswith("@") and "@" not in tokens[1]:
            target += tokens[2]
            consumed += 1
        return target, tokens[consumed:]
    return tokens[0], tokens[1:]


def _cmd_submit_update(client, chat_jid, args: str, session_factory, sender) -> None:
    if not gate(session_factory, sender, client, chat_jid, "member", "update.submit"):
        return
    target, parts = _parse_target(args)
    if len(parts) < 2:
        _reply(client, chat_jid, "Usage: `!update <target> <field> <value>`\nExample: `!update event 4@user@s.whatsapp.net note Ready`")
        return

    try:
        with session_factory() as session:
            submit_update(session, target, parts[0], " ".join(parts[1:]), normalize_jid(sender))
        _reply(client, chat_jid, f"✅ Update submitted for assignment *{target}*.")
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to submit assignment update")
        _reply(client, chat_jid, "❌ An error occurred while submitting the update.")


def _cmd_history(client, chat_jid, args: str, session_factory, sender) -> None:
    if not gate(session_factory, sender, client, chat_jid, "member", "update.history"):
        return
    target, remaining = _parse_target(args)
    if not target or remaining:
        _reply(client, chat_jid, "Usage: `!history <target>`\nExample: `!history task 7@user@s.whatsapp.net`")
        return

    try:
        with session_factory() as session:
            history = get_update_history(session, target)
        if not history:
            _reply(client, chat_jid, f"No updates found for assignment *{target}*.")
            return
        lines = [f"*Update history for assignment {target}*", ""]
        for update in history:
            timestamp = update.timestamp.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"[#{update.id}] [{timestamp}] *{update.field}*: {update.value}")
        _reply(client, chat_jid, "\n".join(lines))
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to fetch assignment update history")
        _reply(client, chat_jid, "❌ An error occurred while fetching history.")


def _cmd_edit_update(client, chat_jid, args: str, session_factory, sender) -> None:
    if not gate(session_factory, sender, client, chat_jid, "member", "update.edit"):
        return
    parts = args.split(None, 1)
    if len(parts) != 2:
        _reply(client, chat_jid, "Usage: `!update-edit <update_id> <new_value>`")
        return

    try:
        with session_factory() as session:
            edit_update(session, parts[0], parts[1], normalize_jid(sender))
        _reply(client, chat_jid, f"✅ Update *#{parts[0]}* edited successfully.")
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to edit assignment update")
        _reply(client, chat_jid, "❌ An error occurred while editing the update.")


def _cmd_status(client, chat_jid, args: str, session_factory, sender) -> None:
    if not gate(session_factory, sender, client, chat_jid, "member", "update.status"):
        return
    target, remaining = _parse_target(args)
    if not target or remaining:
        _reply(client, chat_jid, "Usage: `!status <target>`\nExample: `!status event 4@user@s.whatsapp.net`")
        return

    try:
        with session_factory() as session:
            assignment = get_assignment_status(session, target)
        _reply(
            client,
            chat_jid,
            f"*Status for assignment {target}*\n"
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
    if not gate(session_factory, sender, client, chat_jid, "admin", "update.set_status"):
        return
    target, parts = _parse_target(args)
    if len(parts) != 1:
        _reply(client, chat_jid, "Usage: `!set-status <target> <pending|in_progress|completed|cancelled>`")
        return

    try:
        with session_factory() as session:
            set_assignment_status(session, target, parts[0], normalize_jid(sender))
        _reply(
            client,
            chat_jid,
            f"✅ Status for assignment *{target}* set to *{parts[0].lower()}*.",
        )
    except ValueError as exc:
        _reply(client, chat_jid, f"⚠️ {exc}")
    except Exception:
        log.exception("Failed to set assignment status")
        _reply(client, chat_jid, "❌ An error occurred while updating the status.")


HELP_TEXT = """*Assignment Progress — Command Reference*

Targets can be a numeric assignment ID or a space-based reference:
`event <event_id>@<assignee_jid>` / `task <task_id>@<assignee_jid>`.

`!update <target> <field> <value>`
  Append a progress revision; previous revisions are preserved.

`!update-edit <revision_id> <new_value>`
  Append a corrected revision linked to the previous one.

`!history <target>`
  View every revision for an assignment.

`!status <target>`
  View progress status, reminder state, and missed reminders.

`!set-status <target> <status>`
  Change progress status (admin only).
  Statuses: `pending`, `in_progress`, `completed`, `cancelled`.

Example: `!update event 4@919999999999@s.whatsapp.net note Ready for review`
"""


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

        if lower == "!update-edit" or lower.startswith("!update-edit "):
            _cmd_edit_update(
                client,
                chat,
                body[len("!update-edit"):].strip(),
                session_factory,
                sender,
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
