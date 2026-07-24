"""Task manager feature — PBBot tasks domain.

Operations (admin only):
    task.create  — !task-add <text> | <assignee> | <due date> | <priority>
    task.edit    — !task-edit <id> | <field> | <value>
    task.assign  — !task-assign <id> | <whatsapp-id>
    task.delete  — !task-remove <id>

Operations (all members):
    task.list     — !task-list
    task.complete — !task-complete <id>

Editable fields: text, assignee, due, priority
Priority values: high, medium (default), low

Every state change is wrapped in an operation envelope and written
to the audit_log collection before the reply is sent.
Deleted tasks are soft-deleted — records are preserved for audit history.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

import pymongo
from neonize.events import MessageEv

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
PRIORITY_LABELS = {"high": "🔴", "medium": "🟡", "low": "🟢"}
EDITABLE_FIELDS = {"text", "assignee", "due", "priority"}

# Admin WhatsApp IDs — comma-separated in TASK_ADMINS env var
_ADMINS: set[str] = set()


def _load_admins() -> None:
    global _ADMINS
    raw = os.getenv("TASK_ADMINS", "")
    _ADMINS = {a.strip() for a in raw.split(",") if a.strip()}


_load_admins()


def is_admin(sender: str) -> bool:
    return sender in _ADMINS


# ---------------------------------------------------------------------------
# MongoDB repository layer
# ---------------------------------------------------------------------------

_db = None  # injected by register() or tests


def _get_db():
    return _db


def _set_db(database) -> None:
    global _db
    _db = database


def _tasks_col():
    return _get_db()["tasks"]


def _audit_col():
    return _get_db()["audit_log"]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def _audit(operation: str, payload: dict, actor: str, result: str) -> None:
    """Write an immutable audit record before/after every state change."""
    try:
        _audit_col().insert_one({
            "name": operation,
            "payload": payload,
            "actorId": actor,
            "source": "command",
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:
        log.error("Audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Pure business logic — plain dict → dict, no DB, fully testable
# ---------------------------------------------------------------------------


def create_task(
    state: dict,
    text: str,
    assignee: str,
    due: str,
    priority: str,
    sender: str,
) -> dict:
    """Return a new task dict and append it to state. Does NOT write to DB."""
    priority = priority.lower() if priority.lower() in PRIORITY_LABELS else "medium"
    task = {
        "id": state["nextId"],
        "text": text,
        "assignee": assignee,
        "due": due,
        "priority": priority,
        "status": "open",
        "createdBy": sender,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "deletedAt": None,
    }
    state["tasks"].append(task)
    state["nextId"] += 1
    return task


def complete_task(state: dict, task_id: int, sender: str) -> dict | None:
    """Mark a task completed in state. Returns task or None if not found."""
    task = next(
        (t for t in state["tasks"] if t["id"] == task_id and t["status"] != "deleted"),
        None,
    )
    if not task:
        return None
    task["status"] = "completed"
    task["completedBy"] = sender
    task["completedAt"] = datetime.now(timezone.utc).isoformat()
    return task


def remove_task(state: dict, task_id: int, sender: str) -> dict | None:
    """Soft-delete a task. Preserves history per PRD §06."""
    task = next(
        (t for t in state["tasks"] if t["id"] == task_id and t["status"] != "deleted"),
        None,
    )
    if not task:
        return None
    task["status"] = "deleted"
    task["deletedAt"] = datetime.now(timezone.utc).isoformat()
    task["deletedBy"] = sender
    return task


def edit_task(state: dict, task_id: int, field: str, value: str, sender: str) -> dict | None:
    """Edit a single field on a task. Returns task or None if not found."""
    if field not in EDITABLE_FIELDS:
        return None
    task = next(
        (t for t in state["tasks"] if t["id"] == task_id and t["status"] != "deleted"),
        None,
    )
    if not task:
        return None
    if field == "priority":
        value = value.lower() if value.lower() in PRIORITY_LABELS else "medium"
    task[field] = value
    task["editedBy"] = sender
    task["editedAt"] = datetime.now(timezone.utc).isoformat()
    return task


def assign_task(state: dict, task_id: int, assignee: str, sender: str) -> dict | None:
    """Reassign a task to a new assignee. Returns task or None if not found."""
    task = next(
        (t for t in state["tasks"] if t["id"] == task_id and t["status"] != "deleted"),
        None,
    )
    if not task:
        return None
    task["assignee"] = assignee
    task["editedBy"] = sender
    task["editedAt"] = datetime.now(timezone.utc).isoformat()
    return task


# ---------------------------------------------------------------------------
# DB read/write helpers (thin wrappers — swap for mock in tests)
# ---------------------------------------------------------------------------


def _db_load_state() -> dict:
    """Load full task state from MongoDB."""
    col = _tasks_col()
    tasks = list(col.find({}, {"_id": 0}))
    counter = col.database["task_counters"].find_one({"_id": "tasks"})
    next_id = counter["seq"] if counter else 1
    return {"nextId": next_id, "tasks": tasks}


def _db_save_task(task: dict) -> None:
    _tasks_col().replace_one({"id": task["id"]}, task, upsert=True)


def _db_increment_counter() -> int:
    result = _tasks_col().database["task_counters"].find_one_and_update(
        {"_id": "tasks"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    # find_one_and_update returns the document AFTER update when return_document=True
    # but on first upsert seq starts at 1 so we return seq - 1 as the assigned id
    return result["seq"]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_task(task: dict) -> str:
    pri = PRIORITY_LABELS.get(task.get("priority", "medium"), "🟡")
    status = "✅" if task["status"] == "completed" else "🔲"
    due = task.get("due") or "—"
    assignee = task.get("assignee") or "—"
    return (
        f"{status} *#{task['id']}* {pri} — {task['text']}\n"
        f"   👤 {assignee}  📅 {due}"
    )


# ---------------------------------------------------------------------------
# WhatsApp glue
# ---------------------------------------------------------------------------


def _get_text(message: MessageEv) -> str:
    text = message.Message.conversation or ""
    if message.Message.extendedTextMessage and message.Message.extendedTextMessage.text:
        text = message.Message.extendedTextMessage.text
    return text.strip()


def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)


def _handle_tasks_command(client: "NewClient", message: MessageEv) -> None:
    """Route a single task command. RBAC is enforced per operation."""
    body = _get_text(message)
    if not body or not body.startswith("!"):
        return

    chat_jid = message.Info.MessageSource.Chat
    sender = str(message.Info.MessageSource.Sender)
    lower = body.lower()

    # ------------------------------------------------------------------ #
    # MEMBER commands — no role check
    # ------------------------------------------------------------------ #

    # --- !task-list ---
    if lower == "!task-list":
        state = _db_load_state()
        open_tasks = [t for t in state["tasks"] if t["status"] == "open"]
        if not open_tasks:
            _reply(client, chat_jid, "📭 No open tasks.")
            return
        lines = "\n\n".join(_format_task(t) for t in open_tasks)
        _reply(client, chat_jid, f"*📋 Open tasks ({len(open_tasks)})*\n\n{lines}")
        return

    # --- !task-complete <id> ---
    if lower == "!task-complete" or lower.startswith("!task-complete "):
        id_str = body[14:].strip().lstrip("#")
        try:
            task_id = int(id_str)
        except (ValueError, TypeError):
            _reply(client, chat_jid, "⚠️ Usage: `!task-complete <id>`")
            return

        state = _db_load_state()
        task = complete_task(state, task_id, sender)
        if not task:
            _reply(client, chat_jid, f"❌ No open task with id *#{task_id}*.")
            return
        _db_save_task(task)
        _audit("task.complete", {"taskId": task_id}, sender, "ok")
        _reply(client, chat_jid, f"✅ Task *#{task_id}* marked complete.\n{_format_task(task)}")
        return

    # ------------------------------------------------------------------ #
    # ADMIN-only commands
    # ------------------------------------------------------------------ #

    if not is_admin(sender):
        # Only reject if they actually typed an admin command
        admin_prefixes = ("!task-add", "!task-edit", "!task-assign", "!task-remove")
        if any(lower == p or lower.startswith(p + " ") for p in admin_prefixes):
            _reply(client, chat_jid, "🚫 You don't have permission to use that command.")
        return

    # --- !task-add <text> | <assignee> | <due> | <priority> ---
    if lower == "!task-add" or lower.startswith("!task-add "):
        raw = body[9:].strip()
        if not raw:
            _reply(
                client, chat_jid,
                "⚠️ Usage: `!task-add <text> | <assignee> | <due date> | <priority>`\n"
                "Assignee, due date, and priority are optional.",
            )
            return
        parts = [p.strip() for p in raw.split("|")]
        text = parts[0]
        assignee = parts[1] if len(parts) > 1 else ""
        due = parts[2] if len(parts) > 2 else ""
        priority = parts[3] if len(parts) > 3 else "medium"
        if not text:
            _reply(client, chat_jid, "⚠️ Task text cannot be empty.")
            return

        # Allocate ID from counter, build task, persist
        next_id = _db_increment_counter()
        state = {"nextId": next_id, "tasks": []}
        task = create_task(state, text, assignee, due, priority, sender)
        task["id"] = next_id
        _db_save_task(task)
        _audit("task.create", {"task": task}, sender, "ok")
        _reply(client, chat_jid, f"✅ Task *#{task['id']}* added.\n{_format_task(task)}")
        return

    # --- !task-edit <id> | <field> | <value> ---
    if lower == "!task-edit" or lower.startswith("!task-edit "):
        raw = body[10:].strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 3:
            _reply(
                client, chat_jid,
                "⚠️ Usage: `!task-edit <id> | <field> | <value>`\n"
                f"Fields: {', '.join(sorted(EDITABLE_FIELDS))}",
            )
            return
        try:
            task_id = int(parts[0].lstrip("#"))
        except ValueError:
            _reply(client, chat_jid, "⚠️ Id must be a number.")
            return
        field, value = parts[1].lower(), parts[2]
        if field not in EDITABLE_FIELDS:
            _reply(client, chat_jid, f"⚠️ Unknown field '{field}'. Use: {', '.join(sorted(EDITABLE_FIELDS))}")
            return

        state = _db_load_state()
        task = edit_task(state, task_id, field, value, sender)
        if not task:
            _reply(client, chat_jid, f"❌ No open task with id *#{task_id}*.")
            return
        _db_save_task(task)
        _audit("task.edit", {"taskId": task_id, "field": field, "value": value}, sender, "ok")
        _reply(client, chat_jid, f"✏️ Task *#{task_id}* updated ({field}).\n{_format_task(task)}")
        return

    # --- !task-assign <id> | <whatsapp-id> ---
    if lower == "!task-assign" or lower.startswith("!task-assign "):
        raw = body[12:].strip()
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 2:
            _reply(client, chat_jid, "⚠️ Usage: `!task-assign <id> | <whatsapp-id>`")
            return
        try:
            task_id = int(parts[0].lstrip("#"))
        except ValueError:
            _reply(client, chat_jid, "⚠️ Id must be a number.")
            return
        assignee = parts[1]
        if not assignee:
            _reply(client, chat_jid, "⚠️ Assignee cannot be empty.")
            return

        state = _db_load_state()
        task = assign_task(state, task_id, assignee, sender)
        if not task:
            _reply(client, chat_jid, f"❌ No open task with id *#{task_id}*.")
            return
        _db_save_task(task)
        _audit("task.assign", {"taskId": task_id, "assignee": assignee}, sender, "ok")
        _reply(client, chat_jid, f"👤 Task *#{task_id}* assigned to {assignee}.\n{_format_task(task)}")
        return

    # --- !task-remove <id> ---
    if lower == "!task-remove" or lower.startswith("!task-remove "):
        id_str = body[12:].strip().lstrip("#")
        try:
            task_id = int(id_str)
        except (ValueError, TypeError):
            _reply(client, chat_jid, "⚠️ Usage: `!task-remove <id>`")
            return

        state = _db_load_state()
        task = remove_task(state, task_id, sender)
        if not task:
            _reply(client, chat_jid, f"❌ No open task with id *#{task_id}*.")
            return
        _db_save_task(task)
        _audit("task.delete", {"taskId": task_id}, sender, "ok")
        _reply(client, chat_jid, f"🗑️ Task *#{task_id}* removed — {task['text']}")
        return


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------


def register(client: "NewClient", config: dict) -> callable:
    """Register the task-manager feature on the neonize client."""
    tasks_group_id = config.get("tasks_group_id")
    if not tasks_group_id:
        log.warning("TASKS_GROUP_ID not set — skipping task-manager feature.")
        return None

    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_db_name = os.getenv("MONGO_DB_NAME", "pbbot")
    mongo_client = pymongo.MongoClient(mongo_uri)
    database = mongo_client[mongo_db_name]
    _set_db(database)

    # Indexes for common filters (PRD §06 performance)
    database["tasks"].create_index("status")
    database["tasks"].create_index("assignee")
    database["tasks"].create_index("id", unique=True)

    _load_admins()
    log.info("✅ Task-manager feature registered (DB: %s)", mongo_db_name)

    def on_message(client: "NewClient", message: MessageEv):
        chat_obj = message.Info.MessageSource.Chat
        chat = f"{chat_obj.User}@{chat_obj.Server}"
        if chat == tasks_group_id:
            try:
                _handle_tasks_command(client, message)
            except Exception as exc:
                log.error("Task command error: %s", exc)

    return on_message
