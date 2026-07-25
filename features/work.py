"""Unified user and admin workflow for events, tasks, assignments and progress."""
from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import TYPE_CHECKING

from db.auth import gate, normalize_jid
from db.event_store import EventStore, validate_event_type_category
from db.task_store import TaskStore
from db.work_store import PROGRESS_STATUSES, WorkStore
from db.reminder_store import ReminderStore
from features.subgroups import _get_mentioned_jids, _get_text

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)
WORK_COMMANDS = ("!my", "!work", "!events", "!tasks", "!task",
                 "!update", "!update-edit", "!history", "!status", "!set-status",
                 "!complete-task", "!assign", "!unassign", "!add-task",
                 "!update-task", "!delete-task", "!create-event", "!delete-event")
WORK_SUBCOMMANDS = {"assign", "unassign", "update", "edit", "history", "status", "set-status", "complete", "start", "create", "reminders", "reminder"}


def _format(row: dict) -> str:
    typ = row["target_type"]
    ident = row.get("event_id") if typ == "event" else row.get("task_id")
    who = f" @{row['user_jid'].split('@')[0]}" if row.get("user_jid") else " unassigned"
    due = f" | due {row['due_date'].strftime('%Y-%m-%d')}" if row.get("due_date") else ""
    progress = row.get("status") or "unassigned"
    event_kind = f" | {row['event_type']}/{row['event_category']}" if typ == "event" and row.get("event_type") else ""
    lifecycle = f" | lifecycle `{row['lifecycle_status']}`" if row.get("lifecycle_status") else ""
    return f"• `{typ} {ident}` *{row.get('title', row.get('name', ''))}* — `{progress}`{event_kind}{who}{due}{lifecycle}"


def _parse(args: str):
    """Parse overview filters while accepting the old colon form silently."""
    status = None
    typ = None
    ident = None
    jid = None
    tokens = args.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        low = token.lower()
        if low in PROGRESS_STATUSES:
            status = low
        elif low in ("event", "task") and index + 1 < len(tokens):
            typ = low
            match = re.fullmatch(r"(\d+)(?:@(.+))?", tokens[index + 1])
            if match:
                ident, jid = int(match.group(1)), match.group(2)
                index += 1
                if index + 1 < len(tokens) and tokens[index + 1].startswith("@") and jid is None:
                    jid = tokens[index + 1][1:]
                    index += 1
        elif low.startswith("event:") or low.startswith("task:"):
            match = re.fullmatch(r"(event|task):(\d+)(?:@(.+))?", token, re.I)
            if match:
                typ, ident, jid = match.group(1).lower(), int(match.group(2)), match.group(3)
        elif low.isdigit() and ident is None:
            ident, typ = int(low), "event"
        index += 1
    return status, typ, ident, jid.lstrip("@") if jid else None


def _target(tokens: list[str], start: int = 0):
    """Return (type, id, optional jid, next index) from space-based syntax."""
    if start >= len(tokens):
        raise ValueError("target must start with `event` or `task`")
    legacy = re.fullmatch(r"(event|task):(\d+)(?:@(.+))?", tokens[start], re.I)
    if legacy:
        return legacy.group(1).lower(), int(legacy.group(2)), legacy.group(3), start + 1
    if tokens[start].lower() not in ("event", "task"):
        raise ValueError("target must start with `event` or `task`")
    typ = tokens[start].lower()
    if start + 1 >= len(tokens):
        raise ValueError(f"usage: {typ} <id>")
    match = re.fullmatch(r"(\d+)(?:@(.+))?", tokens[start + 1])
    if not match:
        raise ValueError(f"usage: {typ} <id>")
    ident, jid = int(match.group(1)), match.group(2)
    next_index = start + 2
    if jid is None and next_index < len(tokens) and tokens[next_index].startswith("@"):
        jid, next_index = tokens[next_index][1:], next_index + 1
    return typ, ident, jid, next_index


def _reference(typ: str, ident: int, jid: str | None, sender: str, *, use_sender: bool = True) -> str:
    target_jid = jid or (sender if use_sender else None)
    return f"{typ} {ident}" + (f" @{target_jid}" if target_jid else "")


def _resolve_admin_target(store: WorkStore, typ: str, ident: int, jid: str | None) -> str:
    """Resolve an admin's target without silently choosing a user."""
    if jid:
        return jid
    rows = store.overview(admin=True, target_type=typ, target_id=ident)
    if not rows:
        raise ValueError(f"no assignment exists for {typ} {ident}")
    if len(rows) > 1:
        raise ValueError(f"mention the target user for {typ} {ident}; multiple users are assigned")
    return rows[0]["user_jid"]


def _send(client, chat, text: str) -> None:
    client.send_message(chat, text)


def _legacy_field_args(raw: str) -> dict:
    result = {}
    for part in raw.split("|")[1:]:
        key, _, value = part.strip().partition(" ")
        if not value and ":" in part:
            key, value = (item.strip() for item in part.split(":", 1))
        key, value = key.lower(), value.strip()
        if key in ("description", "desc"):
            result["description"] = value
        elif key == "title":
            result["title"] = value
        elif key in ("priority", "p"):
            result["priority"] = value.lower()
        elif key in ("due", "due_date", "date"):
            try:
                result["due_date"] = datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise ValueError("due date must use YYYY-MM-DD")
        elif key in ("status", "s"):
            result["status"] = value.lower().replace(" ", "_")
    return result


def _create_task(store: TaskStore, raw: str, sender: str):
    parts = [part.strip() for part in raw.split("|")]
    title = parts[0].strip()
    if not title:
        raise ValueError("task title is required")
    fields = _legacy_field_args("|" + "|".join(parts[1:]))
    return store.create(title, sender, description=fields.get("description"),
                        due_date=fields.get("due_date"), priority=fields.get("priority", "medium"))


def _overview(client, chat, store: WorkStore, actor, sender: str, command: str, args: str) -> None:
    status, typ, ident, mentioned_jid = _parse(args)
    is_admin = actor.role == "admin"
    if command in ("!my", "!task"):
        rows = store.overview(user_jid=sender, status=status,
                              target_type="task" if command == "!task" else None,
                              target_id=ident if command == "!task" else None)
        heading = "📌 *My Workload*"
    else:
        alias_type = "event" if command == "!events" else "task" if command == "!tasks" else None
        rows = store.overview(user_jid=None if is_admin else sender, admin=is_admin,
                              status=status, target_type=typ or alias_type, target_id=ident,
                              assignee_jid=mentioned_jid)
        if is_admin:
            rows += store.unassigned(target_type=typ or alias_type)
        heading = "📋 *Work Overview*" if command == "!work" else ("📅 *Events*" if command == "!events" else "✅ *Tasks*")
    if status:
        heading += f" — `{status}`"
    if not rows:
        _send(client, chat, heading + "\n\n📭 No matching work.")
        return
    event_rows = [r for r in rows if r["target_type"] == "event"]
    task_rows = [r for r in rows if r["target_type"] == "task"]
    lines = [heading]
    if event_rows:
        lines += ["", "*Events*"] + [_format(r) for r in event_rows]
    if task_rows:
        lines += ["", "*Tasks*"] + [_format(r) for r in task_rows]
    totals = {s: sum(1 for r in rows if r.get("status") == s) for s in PROGRESS_STATUSES}
    lines += ["", "Totals: " + ", ".join(f"{s}={n}" for s, n in sorted(totals.items()))]
    _send(client, chat, "\n".join(lines))


def _handle_work_subcommand(client, chat, message, actor, sender: str, args: str, factory) -> bool:
    tokens = args.split()
    if not tokens or tokens[0].lower() not in WORK_SUBCOMMANDS:
        if tokens:
            _send(client, chat, "ℹ️ Use `!work` for the overview. Try `!work event <id>`, `!work update event <id> note <text>`, or `!work history event <id>`." )
        return True
    action = tokens[0].lower()
    store = WorkStore(factory)
    is_admin = actor.role == "admin"

    try:
        if action in ("reminders", "reminder"):
            # Reminder controls are part of the unified work workflow. The
            # old !reminders commands remain aliases in features/reminders.
            from features.reminders import (
                _cmd_config, _cmd_history, _cmd_reminders_summary, _cmd_run,
            )
            remainder = args[len(tokens[0]):].strip()
            subcommand, _, sub_args = remainder.partition(" ")
            subcommand = subcommand.lower()
            reminder_store = ReminderStore(factory)
            if not subcommand or subcommand in ("status", "summary"):
                _cmd_reminders_summary(client, chat, reminder_store, actor=actor)
                return True
            if subcommand == "config":
                if not is_admin:
                    _send(client, chat, "⛔ Only administrators can configure reminders.")
                    return True
                mentions = _get_mentioned_jids(message)
                _cmd_config(client, chat, sub_args, actor, mentions, reminder_store)
                return True
            if subcommand == "run":
                if not is_admin:
                    _send(client, chat, "⛔ Only administrators can run reminders.")
                    return True
                _cmd_run(client, chat, actor, reminder_store)
                return True
            if subcommand == "history":
                _cmd_history(client, chat, sub_args, reminder_store, actor=actor)
                return True
            raise ValueError("usage: !work reminders [status|config|run|history [assignment_id]]")

        if action == "create":
            if not is_admin:
                _send(client, chat, "⛔ Only administrators can create work.")
                return True
            parts = [p.strip() for p in args[len(tokens[0]):].strip().split("|")]
            if len(parts) < 2 or parts[0].lower() not in ("event", "task"):
                _send(client, chat, "Usage: `!work create event | <participation|organization> | <category> | <name> | [description]` or `!work create task | <title> | [description] | [due YYYY-MM-DD] | [priority low|medium|high]`")
                return True
            if parts[0].lower() == "event":
                if len(parts) < 4:
                    raise ValueError("event creation needs a type, category, and name")
                event_type, category = validate_event_type_category(parts[1], parts[2])
                event = EventStore(factory).create_event(name=parts[3], type=event_type, category=category, description=parts[4] if len(parts) > 4 else "", status="active")
                _send(client, chat, f"✅ Event `{event['id']}` created: *{event['name']}*")
            else:
                task = _create_task(TaskStore(factory), "|".join(parts[1:]), sender)
                _send(client, chat, f"✅ Task `{task.id}` created: *{task.title}*")
            return True

        if action == "edit":
            if len(tokens) < 3 or not tokens[1].isdigit():
                raise ValueError("usage: !work edit <revision_id> <new value>")
            revision = store.edit_update(int(tokens[1]), " ".join(tokens[2:]), sender)
            _send(client, chat, f"✅ Update `{revision['id']}` edited successfully.")
            return True

        typ, ident, jid, next_index = _target(tokens, 1)
        target_jid = jid or (sender if not is_admin else None)
        if action in ("assign", "unassign"):
            if not is_admin:
                _send(client, chat, "⛔ Only administrators can change assignments.")
                return True
            mentions = _get_mentioned_jids(message)
            target_jid = jid or (normalize_jid(mentions[0]) if mentions else None)
            if not target_jid:
                raise ValueError("mention one user to assign or unassign")
            if action == "assign":
                row = store.assign(typ, ident, target_jid)
                _send(client, chat, f"✅ Assigned `{typ} {ident}` to @{row['user_jid'].split('@')[0]}.")
            else:
                removed = store.unassign(typ, ident, target_jid)
                _send(client, chat, "✅ Assignment removed." if removed else "📭 That user is not assigned to this item.")
            return True

        if action == "history":
            if is_admin:
                target_jid = _resolve_admin_target(store, typ, ident, target_jid)
            history = store.history(_reference(typ, ident, target_jid, sender, use_sender=not is_admin))
            if not history:
                _send(client, chat, "📭 No progress history yet.")
            else:
                lines = [f"🕘 *History — {typ} {ident}*"]
                lines.extend(f"• `{item['field']}`: {item['value']} _(update {item['id']})_" for item in history)
                _send(client, chat, "\n".join(lines))
            return True

        if action == "update":
            if is_admin:
                target_jid = _resolve_admin_target(store, typ, ident, target_jid)
            if next_index >= len(tokens) or not tokens[next_index].strip():
                raise ValueError("usage: !work update event <id> <field> <value>")
            field = tokens[next_index]
            value = " ".join(tokens[next_index + 1:]).strip()
            if not value:
                raise ValueError("update value is required")
            result = store.submit_update(_reference(typ, ident, target_jid, sender, use_sender=not is_admin), field, value, sender)
            _send(client, chat, f"✅ Update `{result['id']}` recorded for `{typ} {ident}`.")
            return True

        status = "completed" if action == "complete" else "in_progress" if action == "start" else None
        if action in ("status", "set-status", "start"):
            if action == "set-status" and next_index >= len(tokens):
                raise ValueError("usage: !work set-status event <id> <status>")
            status = tokens[next_index].lower() if action == "set-status" else status
            if action == "status":
                if is_admin:
                    target_jid = _resolve_admin_target(store, typ, ident, target_jid)
                rows = store.overview(user_jid=None if is_admin else sender, admin=is_admin,
                                      target_type=typ, target_id=ident, assignee_jid=target_jid)
                _send(client, chat, "\n".join([f"📌 *{typ.title()} {ident}*"] + ([_format(row) for row in rows] if rows else ["📭 No assignment found."])))
                return True
            if not is_admin and action == "set-status":
                _send(client, chat, "⛔ Use `!work complete` or update your own work; administrators set explicit statuses.")
                return True
        if action in ("complete", "start") and status:
            if action == "start" and is_admin:
                target_jid = _resolve_admin_target(store, typ, ident, target_jid)
            result = store.set_status(_reference(typ, ident, target_jid, sender, use_sender=not is_admin), status, sender)
            if action == "complete" and typ == "task":
                # Keep the task lifecycle in sync with the assignee's
                # completed progress while retaining separate status fields.
                TaskStore(factory).update(ident, status="done", force_status=True)
            _send(client, chat, f"✅ `{typ} {ident}` marked `{result['status']}`.")
            return True
        if action == "set-status" and status:
            if is_admin:
                target_jid = _resolve_admin_target(store, typ, ident, target_jid)
            result = store.set_status(_reference(typ, ident, target_jid, sender, use_sender=not is_admin), status, sender)
            _send(client, chat, f"✅ `{typ} {ident}` set to `{result['status']}`.")
            return True
    except Exception as exc:
        log.info("work command failed: %s", exc)
        _send(client, chat, f"⚠️ {exc}")
    return True


def handle(client, message, session_factory) -> bool:
    if not message.Info or not message.Info.MessageSource:
        return False
    source = message.Info.MessageSource
    chat = source.Chat
    if getattr(chat, "Server", "") != "g.us":
        return False
    body = _get_text(message)
    command, _, args = body.partition(" ")
    command = command.lower()
    if command not in WORK_COMMANDS:
        return False
    actor = gate(session_factory, source.Sender, client, chat, "member", f"work.{command[1:]}")
    if not actor:
        return True
    sender = normalize_jid(source.Sender)
    if command == "!work" and args.strip().split()[:1] and args.strip().split()[0].lower() in WORK_SUBCOMMANDS:
        return _handle_work_subcommand(client, chat, message, actor, sender, args.strip(), session_factory)
    if command in ("!assign", "!unassign"):
        if actor.role != "admin":
            _send(client, chat, "⛔ Only administrators can change assignments.")
            return True
        parts = [part.strip() for part in args.split("|", 1)]
        head = parts[0].split()
        typ = head[0].lower() if head and head[0].lower() in ("event", "task") else "event"
        ident_token = head[1] if typ in ("event", "task") and len(head) > 1 else (head[0] if head else "")
        mentions = _get_mentioned_jids(message)
        if not ident_token.isdigit() or not mentions:
            _send(client, chat, f"Usage: `{command} {typ} <id> | @user`")
            return True
        target_jid = normalize_jid(mentions[0])
        try:
            if command == "!assign":
                row = WorkStore(session_factory).assign(typ, int(ident_token), target_jid)
                _send(client, chat, f"✅ Assigned `{typ} {ident_token}` to @{row['user_jid'].split('@')[0]}.")
            else:
                removed = WorkStore(session_factory).unassign(typ, int(ident_token), target_jid)
                _send(client, chat, "✅ Assignment removed." if removed else "📭 Assignment not found.")
        except Exception as exc:
            _send(client, chat, f"⚠️ {exc}")
        return True
    if command in ("!add-task", "!update-task", "!delete-task"):
        if actor.role != "admin":
            _send(client, chat, "⛔ Only administrators can manage tasks.")
            return True
        try:
            tasks = TaskStore(session_factory)
            if command == "!add-task":
                task = _create_task(tasks, args, sender)
                _send(client, chat, f"✅ Task `{task.id}` created: *{task.title}*")
            elif command == "!delete-task":
                if not args.strip().isdigit():
                    raise ValueError("usage: !delete-task <id>")
                tasks.delete(int(args.strip()))
                _send(client, chat, f"🗑️ Task `{args.strip()}` deleted.")
            else:
                parts = [part.strip() for part in args.split("|", 1)]
                if len(parts) != 2 or not parts[0].isdigit():
                    raise ValueError("usage: !update-task <id> | field value")
                fields = _legacy_field_args("|" + parts[1])
                task = tasks.update(int(parts[0]), title=fields.get("title"), description=fields.get("description"),
                                    due_date=fields.get("due_date"), priority=fields.get("priority"), status=fields.get("status"), force_status=True)
                _send(client, chat, f"✅ Task `{task.id}` updated.")
        except Exception as exc:
            _send(client, chat, f"⚠️ {exc}")
        return True
    if command in ("!create-event", "!delete-event"):
        if actor.role != "admin":
            _send(client, chat, "⛔ Only administrators can manage events.")
            return True
        try:
            events = EventStore(session_factory)
            if command == "!create-event":
                parts = [part.strip() for part in args.split("|")]
                if len(parts) < 2:
                    raise ValueError("usage: !create-event <type> | <name> | [description]")
                event_type, category = validate_event_type_category(parts[0], "other")
                event = events.create_event(type=event_type, category=category, name=parts[1], description=parts[2] if len(parts) > 2 else "", status="active")
                _send(client, chat, f"✅ Event `{event['id']}` created: *{event['name']}*")
            else:
                if not args.strip().isdigit():
                    raise ValueError("usage: !delete-event <id>")
                if not events.delete_event(int(args.strip())):
                    raise ValueError("event not found")
                _send(client, chat, f"🗑️ Event `{args.strip()}` deleted.")
        except Exception as exc:
            _send(client, chat, f"⚠️ {exc}")
        return True
    legacy_actions = {
        "!update": "update", "!update-edit": "edit", "!history": "history",
        "!status": "status", "!set-status": "set-status",
    }
    if command in legacy_actions:
        if command == "!set-status" and "|" in args:
            if actor.role != "admin":
                _send(client, chat, "⛔ Only administrators can change lifecycle status.")
                return True
            parts = [part.strip() for part in args.split("|", 1)]
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1]:
                _send(client, chat, "Usage: `!set-status <event_id> | <draft|active|completed|cancelled>`")
                return True
            try:
                EventStore(session_factory).set_status(int(parts[0]), parts[1].lower())
                _send(client, chat, f"✅ Event `{parts[0]}` lifecycle set to `{parts[1].lower()}`.")
            except Exception as exc:
                _send(client, chat, f"⚠️ {exc}")
            return True
        return _handle_work_subcommand(client, chat, message, actor, sender,
                                       f"{legacy_actions[command]} {args}".strip(), session_factory)
    if command == "!complete-task":
        return _handle_work_subcommand(client, chat, message, actor, sender,
                                       f"complete task {args}".strip(), session_factory)
    _overview(client, chat, WorkStore(session_factory), actor, sender, command, args)
    return True


def register(client: "NewClient", config: dict) -> callable:
    factory = config.get("db_session_factory")
    if factory is None:
        raise RuntimeError("Work feature requires db_session_factory")
    return lambda client, message: handle(client, message, factory)
