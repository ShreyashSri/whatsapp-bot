"""Unified assignment commands for events and tasks.

Commands:
    !assign [task|event] <id> | @user    — assign to event (default) or task
    !unassign [task|event] <id> [| @user] — unassign from event or task
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.auth import gate, normalize_jid
from db.event_store import EventStore
from db.task_store import TaskStore
from features.subgroups import _get_mentioned_jids, _get_text
from features.text import public_error, public_text, split_command_fields

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)


def _parse_assign_args(args: str) -> tuple[str, int | None, str]:
    """Parse '!assign [task|event] <id> | @user' args.

    Returns (target_type, target_id, remainder_after_pipe).
    target_type is 'event' or 'task'.
    target_id is None when the args are malformed.
    """
    parts = split_command_fields(args, limit=1)
    head = parts[0]
    remainder = parts[1].strip() if len(parts) > 1 else ""

    tokens = head.split()
    if not tokens:
        return "event", None, remainder

    # Detect optional type keyword
    if tokens[0].lower() in ("task", "event"):
        target_type = tokens[0].lower()
        id_token = tokens[1] if len(tokens) > 1 else ""
    else:
        target_type = "event"
        id_token = tokens[0]

    if not id_token.isdigit():
        return target_type, None, remainder

    return target_type, int(id_token), remainder


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_assign(
    client: "NewClient",
    chat: object,
    args: str,
    message: "MessageEv",
    event_store: EventStore,
    task_store: TaskStore,
) -> None:
    target_type, target_id, _ = _parse_assign_args(args)

    if target_id is None:
        _reply(client, chat,
               "⚠️ Usage: `!assign [task|event] <id> | @user`\n"
               "Examples:\n"
               "  `!assign 3 | @person` — assign to event #3\n"
               "  `!assign task 5 | @person` — assign to task #5")
        return

    mentions = _get_mentioned_jids(message)
    if not mentions:
        _reply(client, chat, "⚠️ Mention a user after `|` to assign.")
        return

    assignee_jid = normalize_jid(mentions[0])
    display = f"@{public_text(assignee_jid.split('@')[0], limit=80)}"

    try:
        if target_type == "task":
            task_store.assign(target_id, assignee_jid)
            _reply(client, chat, f"✅ {display} assigned to task #{target_id}.")
        else:
            result = event_store.assign(event_id=target_id, user_id=assignee_jid)
            _reply(client, chat,
                   f"✅ {display} assigned to event #{target_id}. "
                   f"Status: `{result['status']}`")
    except ValueError as exc:
        _reply(client, chat, f"❌ {public_error(exc, 'I could not assign that work item.')}")
    except Exception:
        log.exception("Failed to assign %s #%s", target_type, target_id)
        _reply(client, chat, f"❌ Failed to assign to {target_type} #{target_id}.")


def _cmd_unassign(
    client: "NewClient",
    chat: object,
    args: str,
    message: "MessageEv",
    event_store: EventStore,
    task_store: TaskStore,
) -> None:
    target_type, target_id, _ = _parse_assign_args(args)

    if target_id is None:
        _reply(client, chat,
               "⚠️ Usage: `!unassign [task|event] <id> [| @user]`\n"
               "Examples:\n"
               "  `!unassign 3 | @person` — unassign from event #3\n"
               "  `!unassign task 5` — unassign from task #5")
        return

    try:
        if target_type == "task":
            task_store.unassign(target_id)
            _reply(client, chat, f"✅ Task #{target_id} unassigned.")
        else:
            mentions = _get_mentioned_jids(message)
            if not mentions:
                _reply(client, chat, "⚠️ Mention a user to unassign from an event.")
                return
            assignee_jid = normalize_jid(mentions[0])
            display = f"@{public_text(assignee_jid.split('@')[0], limit=80)}"
            if event_store.unassign(event_id=target_id, user_id=assignee_jid):
                _reply(client, chat, f"✅ {display} unassigned from event #{target_id}.")
            else:
                _reply(client, chat, f"⚠️ {display} is not assigned to event #{target_id}.")
    except ValueError as exc:
        _reply(client, chat, f"❌ {public_error(exc, 'I could not remove that assignment.')}")
    except Exception:
        log.exception("Failed to unassign %s #%s", target_type, target_id)
        _reply(client, chat, f"❌ Failed to unassign from {target_type} #{target_id}.")


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

ASSIGN_CMDS = ("!assign", "!unassign")


def _is_assign_command(body: str) -> bool:
    lower = body.lower()
    return any(lower == cmd or lower.startswith(f"{cmd} ") for cmd in ASSIGN_CMDS)


def register(client: "NewClient", config: dict) -> callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Events (assign) feature requires db_session_factory")

    event_store = EventStore(session_factory)
    task_store = TaskStore(session_factory)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        source = message.Info.MessageSource
        chat = source.Chat

        if getattr(chat, "Server", "") != "g.us":
            return

        body = _get_text(message)
        if not body:
            return

        if not _is_assign_command(body):
            return

        # All assign/unassign commands require admin
        actor = gate(session_factory, source.Sender, client, chat, "admin", "assign")
        if not actor:
            return

        command, _, args = body.partition(" ")
        cmd = command.lower()

        if cmd == "!assign":
            _cmd_assign(client, chat, args, message, event_store, task_store)
        elif cmd == "!unassign":
            _cmd_unassign(client, chat, args, message, event_store, task_store)

    log.info("✅ Events (unified assign) feature registered")
    return on_message
