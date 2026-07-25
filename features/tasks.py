"""Tasks Feature (PRD FR-5).

Admin commands:
  !add-task <title> [| description] [| due YYYY-MM-DD] [| priority low|medium|high]
  !assign-task <id> | @person
  !unassign-task <id>
  !update-task <id> | field: value  (title/desc/due/priority/status)
  !delete-task <id>
  !tasks                            — list all tasks (admin) or own tasks (member)

Member commands:
  !tasks           — list own assigned tasks
  !task <id>       — show task detail
  !complete-task <id>  — mark own task done
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.auth import gate, normalize_jid
from db.task_store import TaskStore, VALID_PRIORITIES, VALID_STATUSES
from features.subgroups import _get_mentioned_jids, _get_text

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

_PRIORITY_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}
_STATUS_EMOJI   = {"todo": "📋", "in_progress": "🔄", "done": "✅", "cancelled": "❌"}

TASK_CMDS = (
    "!add-task", "!tasks", "!task ", "!assign-task",
    "!unassign-task", "!complete-task", "!update-task", "!delete-task",
)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_task(task) -> str:
    s = _STATUS_EMOJI.get(task.status, "")
    p = _PRIORITY_EMOJI.get(task.priority, "")
    due = f" | due {task.due_date.strftime('%Y-%m-%d')}" if task.due_date else ""
    assignee = f" | @{task.assignee_jid.split('@')[0]}" if task.assignee_jid else " | unassigned"
    desc = f"\n  _{task.description}_" if task.description else ""
    return f"{s} *#{task.id}* {task.title}{p}{due}{assignee}{desc}"


def _parse_date(text: str) -> datetime | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_args(args: str) -> dict:
    """Parse pipe-separated key:value pairs from task command args."""
    parts = [p.strip() for p in args.split("|")]
    result: dict = {"title": parts[0] if parts else ""}
    for part in parts[1:]:
        if ":" in part:
            key, _, val = part.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key in ("due", "due_date", "date"):
                result["due_date"] = _parse_date(val)
            elif key in ("priority", "p"):
                result["priority"] = val.lower()
            elif key in ("status", "s"):
                result["status"] = val.lower().replace(" ", "_")
            elif key in ("description", "desc", "d"):
                result["description"] = val
            elif key in ("title",):
                result["title"] = val
        else:
            # bare token: check if it's a priority or status keyword
            low = part.lower().replace(" ", "_")
            if low in VALID_PRIORITIES:
                result["priority"] = low
            elif low in VALID_STATUSES:
                result["status"] = low
            elif part and "title" not in result:
                result["title"] = part
    return result


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_add_task(client, chat, args: str, actor_jid: str, store: TaskStore) -> None:
    parsed = _parse_args(args)
    title = parsed.get("title", "").strip()
    if not title:
        client.send_message(chat, "⚠️ Usage: `!add-task <title> [| description] [| due YYYY-MM-DD] [| priority low|medium|high]`")
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


def _cmd_assign_task(client, chat, args: str, message, store: TaskStore) -> None:
    parts = [p.strip() for p in args.split("|", 1)]
    if not parts[0].isdigit():
        client.send_message(chat, "⚠️ Usage: `!assign-task <id> | @person`")
        return
    task_id = int(parts[0])
    mentions = _get_mentioned_jids(message)
    if not mentions:
        client.send_message(chat, "⚠️ Mention a user after `|`.")
        return
    try:
        task = store.assign(task_id, mentions[0])
        client.send_message(chat, f"✅ Assigned @{mentions[0].split('@')[0]} to task #{task_id}.")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_unassign_task(client, chat, args: str, store: TaskStore) -> None:
    if not args.strip().isdigit():
        client.send_message(chat, "⚠️ Usage: `!unassign-task <id>`")
        return
    try:
        store.unassign(int(args.strip()))
        client.send_message(chat, f"✅ Task #{args.strip()} unassigned.")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_complete_task(client, chat, args: str, actor_jid: str, store: TaskStore, is_admin: bool) -> None:
    if not args.strip().isdigit():
        client.send_message(chat, "⚠️ Usage: `!complete-task <id>`")
        return
    task_id = int(args.strip())
    try:
        # Admins bypass assignee check
        if is_admin:
            task = store.update(task_id, status="done", force_status=True)
        else:
            task = store.complete(task_id, actor_jid)
        client.send_message(chat, f"✅ Task #{task_id} marked as done.")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")


def _cmd_update_task(client, chat, args: str, store: TaskStore) -> None:
    parts = [p.strip() for p in args.split("|", 1)]
    if not parts[0].isdigit():
        client.send_message(chat, "⚠️ Usage: `!update-task <id> | field: value`")
        return
    task_id = int(parts[0])
    parsed = _parse_args(parts[1] if len(parts) > 1 else "")
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


def _cmd_list_tasks(client, chat, actor_jid: str, is_admin: bool, args: str, store: TaskStore) -> None:
    """!tasks — admins see all, members see their own."""
    status_filter = args.strip().lower().replace(" ", "_") if args.strip() in VALID_STATUSES + list(VALID_STATUSES) else None
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
    detail = _fmt_task(task)
    extra = f"\n  Created by: @{task.created_by_jid.split('@')[0]}\n  Created: {task.created_at.strftime('%Y-%m-%d')}"
    client.send_message(chat, f"*Task Detail*\n{detail}{extra}")


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
        if not any(lower == c.rstrip() or lower.startswith(c) for c in TASK_CMDS):
            return

        # All task commands require at least member access
        actor = gate(factory, source.Sender, client, chat, "member", "task")
        if not actor:
            return

        actor_jid = normalize_jid(source.Sender)
        is_admin = actor.role == "admin"

        command, _, args = body.partition(" ")
        cmd = command.lower()

        try:
            if cmd == "!add-task":
                if not is_admin:
                    client.send_message(chat, "⛔ You need to be an active administrator to use this command.")
                    return
                _cmd_add_task(client, chat, args, actor_jid, store)

            elif cmd == "!assign-task":
                if not is_admin:
                    client.send_message(chat, "⛔ You need to be an active administrator to use this command.")
                    return
                _cmd_assign_task(client, chat, args, message, store)

            elif cmd == "!unassign-task":
                if not is_admin:
                    client.send_message(chat, "⛔ You need to be an active administrator to use this command.")
                    return
                _cmd_unassign_task(client, chat, args, store)

            elif cmd == "!update-task":
                if not is_admin:
                    client.send_message(chat, "⛔ You need to be an active administrator to use this command.")
                    return
                _cmd_update_task(client, chat, args, store)

            elif cmd == "!delete-task":
                if not is_admin:
                    client.send_message(chat, "⛔ You need to be an active administrator to use this command.")
                    return
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
