"""Event Management Feature (Core Actions).

Allows admins to create and manage events, and members to view and update
their personal assignment statuses.

Natural language usage:
    @bot create a new participation event Hacktoberfest
    @bot list all events
    @bot show my assigned work
    @bot set event 4 status to completed
    @bot mark event 4 as completed
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from db.event_store import EventStore
from db.auth import gate, normalize_jid
from db.work_store import WorkStore
from features.subgroups import _get_text as _shared_get_text
from features.text import public_error, public_text, split_command_fields

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
    return _shared_get_text(message)


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
            f"• *[{ev['id']}]* {public_text(ev['name'], limit=180)} _({ev['type']})_ [`{ev['status']}`] - 👥 {ev.get('assignment_count', 0)} assigned"
            for ev in events
        ]
        _reply(client, chat_jid, f"*📋 Active Events ({len(events)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to list events: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch events.")


def _cmd_create_event(client: "NewClient", chat_jid, args: str, store: EventStore) -> None:
    """!create-event <type> | <name> | [description]"""
    parts = split_command_fields(args)
    if len(parts) < 2:
        _reply(client, chat_jid, "⚠️ Tell me what event to create (e.g. `@bot create a new participation event Hacktoberfest`).")
        return

    ev_type = parts[0].lower()
    name = parts[1]
    desc = parts[2] if len(parts) > 2 else ""

    if ev_type not in ("participation", "organization"):
        _reply(client, chat_jid, "❌ Event type must be `participation` or `organization`.")
        return

    try:
        event = store.create_event(name=name, type=ev_type, description=desc, status="active")
        _reply(client, chat_jid, f"✅ Event *{public_text(name, limit=180)}* created successfully! (ID: {event['id']})")
    except Exception as exc:
        log.error("Failed to create event: %s", exc)
        _reply(client, chat_jid, "❌ I could not create that event.")


def _cmd_delete_event(client: "NewClient", chat_jid, args: str, store: EventStore) -> None:
    """!delete-event <event_id>"""
    match = re.match(r"^(\d+)$", args)
    if not match:
        _reply(client, chat_jid, "⚠️ Specify the event ID to delete (e.g. `@bot delete event 4`).")
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
    parts = split_command_fields(args)
    if len(parts) != 2 or not parts[0].isdigit():
        _reply(client, chat_jid, "⚠️ Specify the event ID and status (e.g. `@bot set event 4 status to completed`).")
        return
    try:
        updated = store.set_status(event_id=int(parts[0]), status=parts[1].lower())
        _reply(client, chat_jid, f"✅ Event {parts[0]} status changed to `{updated['status']}`.")
    except ValueError as exc:
        _reply(client, chat_jid, f"❌ {public_error(exc, 'I could not update that event status.')}")
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
        lines = []
        for assignment in assignments:
            if assignment.get("target_type") == "task":
                lines.append(
                    f"• *Task {assignment['task_id']}* {public_text(assignment.get('task_name'), limit=180)} "
                    f"under event *[{assignment['event_id']}]* {public_text(assignment['event_name'], limit=180)} "
                    f"- Status: `{assignment['status']}`"
                )
            else:
                lines.append(
                    f"• *[{assignment['event_id']}]* {public_text(assignment['event_name'], limit=180)} "
                    f"_({assignment['event_type']})_ - Status: `{assignment['status']}`"
                )
        _reply(client, chat_jid, f"*📌 Your Assigned Events ({len(assignments)})*\n\n" + "\n".join(lines))
    except Exception as exc:
        log.error("Failed to fetch user assignments: %s", exc)
        _reply(client, chat_jid, "❌ Failed to fetch your assignments.")


def _cmd_my_status(client: "NewClient", chat_jid, sender_jid: str, args: str, store: EventStore) -> None:
    """!my-status <event_id> | <status>"""
    parts = split_command_fields(args)
    if len(parts) < 2 or not parts[0].isdigit():
        _reply(client, chat_jid,
               "⚠️ Specify the event ID and status (e.g. `@bot mark event 4 as completed`).")
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
