"""Tests for features/tasks.py — tasks domain (PBBot).

Run:
    python -m pytest tests/test_tasks.py -v

Test deps (not required in production):
    pip install pytest mongomock
"""

from __future__ import annotations

import types
import pytest
import mongomock


# ---------------------------------------------------------------------------
# Fake WhatsApp objects
# ---------------------------------------------------------------------------

def _make_message(
    text: str,
    chat: str = "11111111111@g.us",
    sender: str = "919999999999@s.whatsapp.net",
):
    """Minimal stand-in for a neonize MessageEv — no neonize import needed."""
    chat_user, chat_server = chat.split("@")
    return types.SimpleNamespace(
        Message=types.SimpleNamespace(
            conversation=text,
            extendedTextMessage=None,
            imageMessage=None,
        ),
        Info=types.SimpleNamespace(
            MessageSource=types.SimpleNamespace(
                Chat=types.SimpleNamespace(User=chat_user, Server=chat_server),
                Sender=sender,
            )
        ),
    )


class FakeClient:
    """Captures send_message calls — no WhatsApp session needed."""

    def __init__(self):
        self.sent: list[tuple] = []

    def send_message(self, chat_jid, text: str):
        self.sent.append((chat_jid, text))

    @property
    def last(self) -> str:
        assert self.sent, "No messages were sent"
        return self.sent[-1][1]

    def clear(self):
        self.sent.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ADMIN_ID = "admin123@s.whatsapp.net"
MEMBER_ID = "member456@s.whatsapp.net"


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    """
    Give every test its own in-memory mongomock database.
    Also patches TASK_ADMINS so ADMIN_ID is always an admin.
    """
    import features.tasks as mod

    client = mongomock.MongoClient()
    db = client["test_pbbot"]
    mod._set_db(db)

    monkeypatch.setenv("TASK_ADMINS", ADMIN_ID)
    mod._load_admins()

    yield db

    # teardown — nothing needed, in-memory


def _admin_msg(text: str) -> types.SimpleNamespace:
    return _make_message(text, sender=ADMIN_ID)


def _member_msg(text: str) -> types.SimpleNamespace:
    return _make_message(text, sender=MEMBER_ID)


# ---------------------------------------------------------------------------
# 1 · Pure logic — no DB, no WhatsApp
# ---------------------------------------------------------------------------

class TestCreateTask:
    def test_basic_fields(self):
        from features.tasks import create_task
        state = {"nextId": 1, "tasks": []}
        task = create_task(state, "Write docs", "Alice", "2026-08-01", "high", "s")
        assert task["text"] == "Write docs"
        assert task["assignee"] == "Alice"
        assert task["due"] == "2026-08-01"
        assert task["priority"] == "high"
        assert task["status"] == "open"
        assert task["createdBy"] == "s"
        assert "createdAt" in task

    def test_unknown_priority_defaults_to_medium(self):
        from features.tasks import create_task
        state = {"nextId": 1, "tasks": []}
        task = create_task(state, "X", "", "", "CRITICAL", "s")
        assert task["priority"] == "medium"

    def test_priority_case_insensitive(self):
        from features.tasks import create_task
        state = {"nextId": 1, "tasks": []}
        task = create_task(state, "X", "", "", "HIGH", "s")
        assert task["priority"] == "high"


class TestCompleteTask:
    def _state(self):
        from features.tasks import create_task
        state = {"nextId": 1, "tasks": []}
        create_task(state, "Fix bug", "Bob", "2026-08-10", "medium", "s")
        return state

    def test_marks_completed(self):
        from features.tasks import complete_task
        state = self._state()
        task = complete_task(state, 1, "s2")
        assert task["status"] == "completed"
        assert task["completedBy"] == "s2"
        assert "completedAt" in task

    def test_returns_none_for_missing(self):
        from features.tasks import complete_task
        assert complete_task({"nextId": 1, "tasks": []}, 99, "s") is None

    def test_deleted_task_not_completable(self):
        from features.tasks import create_task, remove_task, complete_task
        state = {"nextId": 1, "tasks": []}
        create_task(state, "X", "", "", "low", "s")
        remove_task(state, 1, "s")
        assert complete_task(state, 1, "s") is None


class TestRemoveTask:
    def test_soft_delete_preserves_record(self):
        from features.tasks import create_task, remove_task
        state = {"nextId": 1, "tasks": []}
        create_task(state, "Keep history", "", "", "low", "s")
        removed = remove_task(state, 1, "s")
        assert removed["status"] == "deleted"
        assert "deletedAt" in removed
        assert len(state["tasks"]) == 1  # still in list, not popped

    def test_returns_none_for_missing(self):
        from features.tasks import remove_task
        assert remove_task({"nextId": 1, "tasks": []}, 42, "s") is None

    def test_already_deleted_returns_none(self):
        from features.tasks import create_task, remove_task
        state = {"nextId": 1, "tasks": []}
        create_task(state, "X", "", "", "low", "s")
        remove_task(state, 1, "s")
        assert remove_task(state, 1, "s") is None  # second remove fails


class TestEditTask:
    def _state(self):
        from features.tasks import create_task
        state = {"nextId": 1, "tasks": []}
        create_task(state, "Original", "Alice", "2026-08-01", "low", "s")
        return state

    def test_edit_text(self):
        from features.tasks import edit_task
        state = self._state()
        task = edit_task(state, 1, "text", "Updated text", "s")
        assert task["text"] == "Updated text"

    def test_edit_assignee(self):
        from features.tasks import edit_task
        state = self._state()
        task = edit_task(state, 1, "assignee", "Bob", "s")
        assert task["assignee"] == "Bob"

    def test_edit_priority_normalised(self):
        from features.tasks import edit_task
        state = self._state()
        task = edit_task(state, 1, "priority", "HIGH", "s")
        assert task["priority"] == "high"

    def test_invalid_field_returns_none(self):
        from features.tasks import edit_task
        state = self._state()
        assert edit_task(state, 1, "status", "hacked", "s") is None

    def test_missing_id_returns_none(self):
        from features.tasks import edit_task
        state = self._state()
        assert edit_task(state, 99, "text", "X", "s") is None


class TestAssignTask:
    def test_reassigns(self):
        from features.tasks import create_task, assign_task
        state = {"nextId": 1, "tasks": []}
        create_task(state, "Task", "Alice", "", "low", "s")
        task = assign_task(state, 1, "Carol", "s")
        assert task["assignee"] == "Carol"

    def test_missing_id_returns_none(self):
        from features.tasks import assign_task
        assert assign_task({"nextId": 1, "tasks": []}, 99, "X", "s") is None


# ---------------------------------------------------------------------------
# 2 · DB persistence (mongomock — no real MongoDB)
# ---------------------------------------------------------------------------

class TestDBPersistence:
    def test_save_and_reload(self):
        from features.tasks import _db_save_task, _db_load_state, _db_increment_counter
        _db_increment_counter()  # seq → 1
        task = {
            "id": 1, "text": "Persisted", "assignee": "Dave",
            "due": "2026-09-01", "priority": "high", "status": "open",
            "createdBy": "s", "createdAt": "2026-07-24T00:00:00+00:00", "deletedAt": None,
        }
        _db_save_task(task)
        state = _db_load_state()
        assert len(state["tasks"]) == 1
        assert state["tasks"][0]["text"] == "Persisted"

    def test_counter_increments(self):
        from features.tasks import _db_increment_counter
        id1 = _db_increment_counter()
        id2 = _db_increment_counter()
        assert id2 == id1 + 1


class TestAuditLog:
    def test_audit_record_written(self, fresh_db):
        from features.tasks import _audit
        _audit("task.create", {"taskId": 1}, "actor@s.whatsapp.net", "ok")
        records = list(fresh_db["audit_log"].find({}))
        assert len(records) == 1
        assert records[0]["name"] == "task.create"
        assert records[0]["actorId"] == "actor@s.whatsapp.net"
        assert records[0]["result"] == "ok"

    def test_audit_immutable_grows(self, fresh_db):
        from features.tasks import _audit
        _audit("task.create", {}, "a", "ok")
        _audit("task.complete", {}, "b", "ok")
        assert fresh_db["audit_log"].count_documents({}) == 2


# ---------------------------------------------------------------------------
# 3 · RBAC
# ---------------------------------------------------------------------------

class TestRBAC:
    def test_is_admin_true(self):
        from features.tasks import is_admin
        assert is_admin(ADMIN_ID) is True

    def test_is_admin_false(self):
        from features.tasks import is_admin
        assert is_admin(MEMBER_ID) is False

    def test_member_blocked_from_task_add(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _member_msg("!task-add Sneaky task"))
        assert "permission" in client.last.lower()

    def test_member_blocked_from_task_remove(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _member_msg("!task-remove 1"))
        assert "permission" in client.last.lower()

    def test_member_blocked_from_task_edit(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _member_msg("!task-edit 1 | text | hack"))
        assert "permission" in client.last.lower()

    def test_member_blocked_from_task_assign(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _member_msg("!task-assign 1 | attacker"))
        assert "permission" in client.last.lower()

    def test_member_can_list(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _member_msg("!task-list"))
        # Should reply with empty list, not a permission error
        assert "permission" not in client.last.lower()

    def test_member_can_complete(self):
        from features.tasks import _handle_tasks_command, _db_save_task, _db_increment_counter
        # Admin adds a task directly to DB first
        _db_increment_counter()
        _db_save_task({
            "id": 1, "text": "Do it", "assignee": MEMBER_ID,
            "due": "", "priority": "low", "status": "open",
            "createdBy": ADMIN_ID, "createdAt": "2026-07-24T00:00:00+00:00", "deletedAt": None,
        })
        client = FakeClient()
        _handle_tasks_command(client, _member_msg("!task-complete 1"))
        assert "permission" not in client.last.lower()
        assert "complete" in client.last.lower()


# ---------------------------------------------------------------------------
# 4 · Command parsing (admin sender, all commands)
# ---------------------------------------------------------------------------

class TestTaskAddCommand:
    def test_full_fields(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Fix login | Alice | 2026-08-01 | high"))
        assert "Fix login" in client.last
        assert "#1" in client.last

    def test_empty_body_shows_usage(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add"))
        assert "Usage" in client.last

    def test_optional_fields_default(self):
        from features.tasks import _handle_tasks_command, _db_load_state
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Just a title"))
        state = _db_load_state()
        assert state["tasks"][0]["priority"] == "medium"
        assert state["tasks"][0]["assignee"] == ""

    def test_non_command_ignored(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("hello world"))
        assert client.sent == []

    def test_audit_written_on_add(self, fresh_db):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Audited | | | low"))
        assert fresh_db["audit_log"].count_documents({"name": "task.create"}) == 1


class TestTaskListCommand:
    def test_empty(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-list"))
        assert "No open tasks" in client.last

    def test_shows_open_tasks(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Review PR | Bob | 2026-08-05 | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-list"))
        assert "Review PR" in client.last

    def test_completed_not_shown(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Done | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-complete 1"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-list"))
        assert "No open tasks" in client.last

    def test_deleted_not_shown(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add To delete | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-remove 1"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-list"))
        assert "No open tasks" in client.last


class TestTaskCompleteCommand:
    def test_completes_task(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Deploy | | | medium"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-complete 1"))
        assert "complete" in client.last.lower()

    def test_missing_id_error(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-complete 999"))
        assert "❌" in client.last

    def test_bad_id_usage(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-complete abc"))
        assert "Usage" in client.last

    def test_audit_written(self, fresh_db):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add X | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-complete 1"))
        assert fresh_db["audit_log"].count_documents({"name": "task.complete"}) == 1


class TestTaskEditCommand:
    def test_edit_text(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Old title | | | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-edit 1 | text | New title"))
        assert "New title" in client.last

    def test_edit_priority(self):
        from features.tasks import _handle_tasks_command, _db_load_state
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Task | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-edit 1 | priority | high"))
        state = _db_load_state()
        assert state["tasks"][0]["priority"] == "high"

    def test_invalid_field(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Task | | | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-edit 1 | status | hacked"))
        assert "Unknown field" in client.last

    def test_missing_id_error(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-edit 99 | text | X"))
        assert "❌" in client.last

    def test_bad_format_shows_usage(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-edit 1"))
        assert "Usage" in client.last

    def test_audit_written(self, fresh_db):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Task | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-edit 1 | text | Updated"))
        assert fresh_db["audit_log"].count_documents({"name": "task.edit"}) == 1


class TestTaskAssignCommand:
    def test_reassigns(self):
        from features.tasks import _handle_tasks_command, _db_load_state
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Task | Alice | | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-assign 1 | Carol"))
        state = _db_load_state()
        assert state["tasks"][0]["assignee"] == "Carol"
        assert "Carol" in client.last

    def test_missing_id_error(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-assign 99 | Carol"))
        assert "❌" in client.last

    def test_bad_format_shows_usage(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-assign 1"))
        assert "Usage" in client.last

    def test_audit_written(self, fresh_db):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Task | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-assign 1 | Carol"))
        assert fresh_db["audit_log"].count_documents({"name": "task.assign"}) == 1


class TestTaskRemoveCommand:
    def test_soft_delete(self):
        from features.tasks import _handle_tasks_command, _db_load_state
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Temp | | | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-remove 1"))
        assert "🗑️" in client.last
        # Record still in DB (soft delete)
        state = _db_load_state()
        assert state["tasks"][0]["status"] == "deleted"

    def test_missing_id_error(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-remove 999"))
        assert "❌" in client.last

    def test_audit_written(self, fresh_db):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add X | | | low"))
        _handle_tasks_command(client, _admin_msg("!task-remove 1"))
        assert fresh_db["audit_log"].count_documents({"name": "task.delete"}) == 1


class TestHashIdPrefix:
    def test_complete_with_hash(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Hash test | | | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-complete #1"))
        assert "❌" not in client.last

    def test_remove_with_hash(self):
        from features.tasks import _handle_tasks_command
        client = FakeClient()
        _handle_tasks_command(client, _admin_msg("!task-add Hash remove | | | low"))
        client.clear()
        _handle_tasks_command(client, _admin_msg("!task-remove #1"))
        assert "🗑️" in client.last
