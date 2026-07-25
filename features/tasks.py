"""Tasks Feature (PRD FR-5).

Assignment is handled by the unified !assign / !unassign commands in events.py.

Admin commands:
  !add-task <title> [| desc] [| due YYYY-MM-DD] [| priority low|medium|high]
  !update-task <id> | field: value  (title/desc/due/priority/status)
  !delete-task <id>
  !tasks                  — list all tasks

Member commands:
  !tasks                  — list own assigned tasks
  !task <id>              — show task detail
  !complete-task <id>     — mark own task done
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.auth import gate, normalize_jid
from db.task_store import TaskStore, VALID_PRIORITIES, VALID_STATUSES
from features.subgroups import _get_text

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

_PRIORITY_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}
_STATUS_EMOJI   = {"todo": "📋", "in_progress": "🔄", "done": "✅", "cancelled": "❌"}

TASK_CMDS = (
    "!add-task", "!tasks", "!task", "!complete-task",
    "!update-task", "!delete-task",
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_task(task) -> str:
    s = _STATUS_EMOJI.get(task.status, "")
    p = _PRIORITY_EMOJI.get(task.priority, "")
    due = f" | due {task.due_date.strftime('%Y-%m-%d')}" if task.due_date else ""
    assignee = (
        f" | @{task.assignee_jid.split('@')[0]}" if task.assignee_jid else " | unassigned"
    )
    desc = f"\n  _{task.description}_" if task.description else ""
    return f"{s} *#{task.id}* {task.title} {p}{due}{assignee}{desc}"


def _parse_date(text: str) -> datetime | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_args(args: str, *, include_title: bool = True) -> dict:
    """Parse task creation fields or update ``key: value`` fields."""
    parts = [p.strip() for p in args.split("|")]
    result: dict = {}
    if include_title:
        result["title"] = parts[0] if parts else ""
    fields = parts[1:] if include_title else parts
    for index, part in enumerate(fields):
        if ":" in part:
            key, _, val = part.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key in ("due", "due_date", "date"):
                result["due_date"] = _parse_date(val)
            elif key in ("priority", "p"):
                result["priority"] = val.lower()
            elif key in ("status", "s"):
                result["status"] = val.lower().replace(" ", "_")
            elif key in ("description", "desc", "d"):
                result["description"] = val
            elif key == "title":
                result["title"] = val
        else:
            low = part.lower().replace(" ", "_")
            if low in VALID_PRIORITIES:
                result["priority"] = low
            elif low in VALID_STATUSES:
                result["status"] = low
            elif include_title and index == 0 and part:
                result["description"] = part
    return result


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_add_task(client, chat, args: str, actor_jid: str, store: TaskStore) -> None:
    parsed = _parse_args(args)
    title = parsed.get("title", "").strip()
    if not title:
        client.send_message(
            chat,
            "⚠️ Usage: `!add-task <title> [| description] [| due YYYY-MM-DD] [| priority low|medium|high]`",
        )
        return
    try:
        task = store.create(
            title=title,
            created_by_jid=actor_jid,
            description=parsed.get("description"),
            due_date=parsed.get("due_date"),
            priority=parsed.get("priority", "medium"),
        )
        client.send_message(chat, f"✅ Task created!\n{_fmt_task(task)}")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_complete_task(
    client, chat, args: str, actor_jid: str, store: TaskStore, is_admin: bool
) -> None:
    if not args.strip().isdigit():
        client.send_message(chat, "⚠️ Usage: `!complete-task <id>`")
        return
    task_id = int(args.strip())
    try:
        if is_admin:
            store.update(task_id, status="done", force_status=True)
        else:
            store.complete(task_id, actor_jid)
        client.send_message(chat, f"✅ Task #{task_id} marked as done.")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_update_task(client, chat, args: str, store: TaskStore) -> None:
    parts = [p.strip() for p in args.split("|", 1)]
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1]:
        client.send_message(chat, "⚠️ Usage: `!update-task <id> | field: value`")
        return
    task_id = int(parts[0])
    parsed = _parse_args(parts[1], include_title=False)
    try:
        task = store.update(
            task_id,
            title=parsed.get("title") or None,
            description=parsed.get("description"),
            due_date=parsed.get("due_date"),
            priority=parsed.get("priority"),
            status=parsed.get("status"),
            force_status=True,
        )
        client.send_message(chat, f"✅ Task updated!\n{_fmt_task(task)}")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_delete_task(client, chat, args: str, store: TaskStore) -> None:
    if not args.strip().isdigit():
        client.send_message(chat, "⚠️ Usage: `!delete-task <id>`")
        return
    try:
        store.delete(int(args.strip()))
        client.send_message(chat, f"🗑️ Task #{args.strip()} deleted.")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_list_tasks(
    client, chat, actor_jid: str, is_admin: bool, args: str, store: TaskStore
) -> None:
    status_filter = (
        args.strip().lower().replace(" ", "_")
        if args.strip().replace(" ", "_") in VALID_STATUSES
        else None
    )
    if is_admin:
        tasks = store.list_all(status=status_filter)
        header = f"*📋 All Tasks* ({len(tasks)})"
    else:
        tasks = store.list_for_user(actor_jid, status=status_filter)
        header = f"*📋 My Tasks* ({len(tasks)})"

    if not tasks:
        client.send_message(chat, "📭 No tasks found.")
    else:
        lines = "\n".join(_fmt_task(t) for t in tasks)
        client.send_message(chat, f"{header}\n\n{lines}")


def _cmd_task_info(client, chat, args: str, store: TaskStore) -> None:
    if not args.strip().isdigit():
        client.send_message(chat, "⚠️ Usage: `!task <id>`")
        return
    task = store.get(int(args.strip()))
    if not task:
        client.send_message(chat, "❌ Task not found.")
        return
    extra = (
        f"\n  Created by: @{task.created_by_jid.split('@')[0]}"
        f"\n  Created: {task.created_at.strftime('%Y-%m-%d')}"
    )
    client.send_message(chat, f"*Task Detail*\n{_fmt_task(task)}{extra}")


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    factory = config.get("db_session_factory")
    if factory is None:
        raise RuntimeError("Tasks feature requires db_session_factory")
    store = TaskStore(factory)

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

        lower = body.lower()
        if not any(lower == cmd or lower.startswith(f"{cmd} ") for cmd in TASK_CMDS):
            return

        command, _, args = body.partition(" ")
        cmd = command.lower()
        required_role = "admin" if cmd in ("!add-task", "!update-task", "!delete-task") else "member"
        actor = gate(factory, source.Sender, client, chat, required_role, f"task.{cmd[1:]}")
        if not actor:
            return

        actor_jid = normalize_jid(source.Sender)
        is_admin = actor.role == "admin"

        try:
            if cmd == "!add-task":
                _cmd_add_task(client, chat, args, actor_jid, store)

            elif cmd == "!update-task":
                _cmd_update_task(client, chat, args, store)

            elif cmd == "!delete-task":
                _cmd_delete_task(client, chat, args, store)

            elif cmd == "!complete-task":
                _cmd_complete_task(client, chat, args, actor_jid, store, is_admin)

            elif cmd == "!tasks":
                _cmd_list_tasks(client, chat, actor_jid, is_admin, args, store)

            elif cmd == "!task":
                _cmd_task_info(client, chat, args, store)

        except Exception as exc:
            log.exception("Unhandled error in tasks feature: %s", exc)
            client.send_message(chat, f"❌ Unexpected error: {exc}")

    log.info("✅ Tasks feature registered")
    return on_message
