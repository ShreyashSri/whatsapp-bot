"""End-to-end test of the unified `!my` / `!work` command surface.

Drives features/work.py exactly the way bot.py does — through mocked
neonize MessageEv objects against an in-memory SQLite database — covering
create, assign, overview, status, update, history, edit, and reminders.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.auth import upsert_user
from db.event_store import EventStore
from db.models import Assignment, Base, ProgressRevision, Task
from db.task_store import TaskStore
from db.work_store import WorkStore
from features.work import _add_child_task_assignees, _format, _get_display_name_map, register as register_work


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def admin_user(db_session_factory):
    return upsert_user(db_session_factory, "admin@s.whatsapp.net", role="admin", display_name="Admin")


@pytest.fixture
def member_user(db_session_factory):
    return upsert_user(db_session_factory, "member@s.whatsapp.net", role="member", display_name="Member")


@pytest.fixture
def handler(db_session_factory):
    mock_client = MagicMock()
    config = {"db_session_factory": db_session_factory}
    return mock_client, register_work(mock_client, config)


def make_msg(text: str, sender_jid: str, server: str = "g.us"):
    msg = MagicMock()
    msg.Info.MessageSource.Chat.Server = server
    msg.Info.MessageSource.Sender = sender_jid
    msg.Message.conversation = text
    msg.Message.extendedTextMessage = None
    msg.Message.imageMessage = None
    return msg


def last_reply(mock_client) -> str:
    reply = mock_client.send_message.call_args[0][1]
    text = getattr(getattr(reply, "extendedTextMessage", None), "text", None)
    return text or reply


def test_work_name_lookup_uses_neonize_jid_objects():
    from types import SimpleNamespace

    seen = []

    class Client:
        def get_group_info(self, _chat):
            return SimpleNamespace(Participants=[])

        def get_user_info(self, *jids):
            seen.extend(jids)
            return []

    _get_display_name_map(Client(), "120@g.us", ["919606214389@s.whatsapp.net"])

    assert len(seen) == 1
    assert getattr(seen[0], "User", "") == "919606214389"
    assert getattr(seen[0], "Server", "") == "s.whatsapp.net"


def test_event_overview_shows_assignees_inherited_from_child_tasks():
    rows = [
        {
            "target_type": "event",
            "event_id": 3,
            "title": "lfx",
            "status": None,
            "user_jid": None,
            "lifecycle_status": "active",
        },
        {
            "target_type": "task",
            "task_id": 8,
            "parent_event_id": 3,
            "title": "tell result",
            "status": "in_progress",
            "user_jid": "shuvam@s.whatsapp.net",
            "lifecycle_status": "in_progress",
        },
    ]

    _add_child_task_assignees(rows)

    event_line = _format(rows[0])
    assert "task-assigned" in event_line
    assert "tasks assigned to @+shuvam" in event_line


def test_full_work_lifecycle(db_session_factory, handler, admin_user, member_user):
    mock_client, run = handler

    # 1. Admin creates an event and a task.
    mock_client.reset_mock()
    run(mock_client, make_msg(
        "!work create event | organization | workshop | Backend Workshop | Intro session",
        admin_user.jid))
    reply = last_reply(mock_client)
    assert "✅ Event" in reply and "created" in reply
    event_id = int(reply.split("`")[1])

    mock_client.reset_mock()
    run(mock_client, make_msg(
        "!work create task | Prepare slides | Slides for workshop | due 2026-08-01 | priority high",
        admin_user.jid))
    reply = last_reply(mock_client)
    assert "✅ Task" in reply and "created" in reply
    task_id = int(reply.split("`")[1])

    # Non-admin cannot create.
    mock_client.reset_mock()
    run(mock_client, make_msg("!work create task | Sneaky task", member_user.jid))
    assert "⛔" in last_reply(mock_client)

    # 2. Admin assigns both to the member (mention parsing is exercised
    # separately for community_tag/subgroups; here we stub the extracted
    # mention list the same way WhatsApp's contextInfo would produce it).
    with patch("features.work._get_mentioned_jids", return_value=[member_user.jid]):
        mock_client.reset_mock()
        run(mock_client, make_msg(f"!work assign event {event_id} | @Member", admin_user.jid))
        assert "✅ Assigned" in last_reply(mock_client)

        mock_client.reset_mock()
        run(mock_client, make_msg(f"!work assign task {task_id} | @Member", admin_user.jid))
        assert "✅ Assigned" in last_reply(mock_client)

    # 3. Member sees both in `!my`.
    mock_client.reset_mock()
    run(mock_client, make_msg("!my", member_user.jid))
    reply = last_reply(mock_client)
    assert "Backend Workshop" in reply and "Prepare slides" in reply

    # 4. Member inspects a single target.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work event {event_id}", member_user.jid))
    assert "Backend Workshop" in last_reply(mock_client)

    # 5. Member starts the event (pending -> in_progress).
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work start event {event_id}", member_user.jid))
    assert "in_progress" in last_reply(mock_client)

    # 6. Member records progress.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work update event {event_id} prs 3", member_user.jid))
    reply = last_reply(mock_client)
    assert "✅ Update" in reply
    revision_id = int(reply.split("`")[1])

    # 7. History shows the revision.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work history event {event_id}", member_user.jid))
    reply = last_reply(mock_client)
    assert "prs" in reply and "3" in reply

    # 8. Edit appends a correction, visible in history afterwards.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work edit {revision_id} 5", member_user.jid))
    assert "edited successfully" in last_reply(mock_client)

    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work history event {event_id}", member_user.jid))
    assert "5" in last_reply(mock_client)

    # 9. Admin inspects assignment status without needing to name the user
    # (only one assignee exists for this event).
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work status event {event_id}", admin_user.jid))
    assert "in_progress" in last_reply(mock_client)

    # 10. Admin force-sets status via the inline @jid form.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work set-status event {event_id} @{member_user.jid} completed", admin_user.jid))
    assert "completed" in last_reply(mock_client)

    # Non-admin may not set-status directly.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work set-status event {event_id} completed", member_user.jid))
    assert "⛔" in last_reply(mock_client)

    # 11. Member completes the task; task lifecycle syncs to "done".
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work complete task {task_id}", member_user.jid))
    assert "completed" in last_reply(mock_client)

    # 12. `!work` overview (admin) reflects both completed items.
    mock_client.reset_mock()
    run(mock_client, make_msg("!work", admin_user.jid))
    reply = last_reply(mock_client)
    assert "Backend Workshop" in reply and "Prepare slides" in reply
    assert "completed=2" in reply

    # 13. Reminders are reachable through the unified `!work reminders` path.
    mock_client.reset_mock()
    run(mock_client, make_msg("!work reminders", member_user.jid))
    assert "Reminder System Status" in last_reply(mock_client)

    mock_client.reset_mock()
    run(mock_client, make_msg(
        "!work reminders config frequency 12 | window 09:00-18:00 | threshold 3 | channel @admin",
        admin_user.jid))
    reply = last_reply(mock_client)
    assert "✅ Reminder config updated!" in reply and "12h" in reply

    mock_client.reset_mock()
    run(mock_client, make_msg("!work reminders run", admin_user.jid))
    assert "Reminder Run Completed" in last_reply(mock_client)

    mock_client.reset_mock()
    run(mock_client, make_msg("!work reminders history", admin_user.jid))
    # Nothing missed yet (both items completed), so history may be empty —
    # the command should still respond without error.
    assert mock_client.send_message.called


def test_unassigned_member_cannot_act_on_others_work(db_session_factory, handler, admin_user, member_user):
    mock_client, run = handler
    other = upsert_user(db_session_factory, "other@s.whatsapp.net", role="member", display_name="Other")

    mock_client.reset_mock()
    run(mock_client, make_msg("!work create event | participation | gsoc | GSoC 2026 | Track applicants", admin_user.jid))
    event_id = int(last_reply(mock_client).split("`")[1])

    with patch("features.work._get_mentioned_jids", return_value=[other.jid]):
        mock_client.reset_mock()
        run(mock_client, make_msg(f"!work assign event {event_id} | @Other", admin_user.jid))

    # member (not assigned) tries to update someone else's event — resolves
    # to their own (nonexistent) assignment, not other's.
    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work update event {event_id} note hijack", member_user.jid))
    assert "⚠️" in last_reply(mock_client)


def test_dm_member_and_admin_updates_keep_task_and_assignment_linked(
    db_session_factory, handler, admin_user, member_user
):
    mock_client, run = handler
    event = EventStore(db_session_factory).create_event(
        name="DM event", type="organization", category="workshop"
    )
    task = TaskStore(db_session_factory).create(
        "DM task", admin_user.jid, event_id=event["id"]
    )
    WorkStore(db_session_factory).assign("event", event["id"], member_user.jid)
    WorkStore(db_session_factory).assign("task", task.id, member_user.jid)

    mock_client.reset_mock()
    run(
        mock_client,
        make_msg(
            f"!work update event {event['id']} note admin correction",
            admin_user.jid,
            server="s.whatsapp.net",
        ),
    )
    assert "✅ Update" in last_reply(mock_client)

    mock_client.reset_mock()
    run(
        mock_client,
        make_msg(
            f"!work start task {task.id}",
            member_user.jid,
            server="s.whatsapp.net",
        ),
    )
    assert "in_progress" in last_reply(mock_client)

    mock_client.reset_mock()
    run(
        mock_client,
        make_msg(
            f"!work update task {task.id} note member progress",
            member_user.jid,
            server="s.whatsapp.net",
        ),
    )
    assert "✅ Update" in last_reply(mock_client)

    other = upsert_user(
        db_session_factory, "other@s.whatsapp.net", role="member", display_name="Other"
    )
    other_task = TaskStore(db_session_factory).create(
        "Other DM task", admin_user.jid, event_id=event["id"]
    )
    WorkStore(db_session_factory).assign("task", other_task.id, other.jid)

    mock_client.reset_mock()
    run(
        mock_client,
        make_msg(
            f"!work update task {other_task.id} note cross-user attempt",
            member_user.jid,
            server="s.whatsapp.net",
        ),
    )
    assert "own assignment" in last_reply(mock_client)
    with db_session_factory() as session:
        assignment = session.query(Assignment).filter_by(task_id=other_task.id).one()
        assert not session.query(ProgressRevision).filter_by(
            assignment_id=assignment.id
        ).first()

    mock_client.reset_mock()
    run(
        mock_client,
        make_msg(
            f"!work set-status task {task.id} completed",
            admin_user.jid,
            server="s.whatsapp.net",
        ),
    )
    assert "completed" in last_reply(mock_client)

    with db_session_factory() as session:
        assert session.get(Task, task.id).event_id == event["id"]
        assert session.get(Task, task.id).status == "done"
        assignment = session.query(Assignment).filter_by(task_id=task.id).one()
        assert assignment.status == "completed"
        fields = {
            revision.field
            for revision in session.query(ProgressRevision).filter_by(
                assignment_id=assignment.id
            ).all()
        }
        assert fields == {"status", "note"}


def test_admin_can_complete_unassigned_task_and_multi_assignee_completion_is_explicit(
    db_session_factory, handler, admin_user, member_user
):
    mock_client, run = handler
    unassigned = TaskStore(db_session_factory).create("Unassigned task", admin_user.jid)

    run(
        mock_client,
        make_msg(f"!work complete task {unassigned.id}", admin_user.jid),
    )
    assert "unassigned" in last_reply(mock_client)
    with db_session_factory() as session:
        assert session.get(Task, unassigned.id).status == "done"

    status_only = TaskStore(db_session_factory).create("Status-only task", admin_user.jid)
    mock_client.reset_mock()
    run(
        mock_client,
        make_msg(f"!work set-status task {status_only.id} in_progress", admin_user.jid),
    )
    assert "in_progress" in last_reply(mock_client)
    with db_session_factory() as session:
        assert session.get(Task, status_only.id).status == "in_progress"

    other = upsert_user(
        db_session_factory, "other@s.whatsapp.net", display_name="Other"
    )
    shared = TaskStore(db_session_factory).create("Shared task", admin_user.jid)
    WorkStore(db_session_factory).assign("task", shared.id, member_user.jid)
    WorkStore(db_session_factory).assign("task", shared.id, other.jid)

    mock_client.reset_mock()
    run(mock_client, make_msg(f"!work complete task {shared.id}", admin_user.jid))
    assert "multiple users are assigned" in last_reply(mock_client)
    with db_session_factory() as session:
        assert session.get(Task, shared.id).status == "todo"


def test_task_event_and_person_links_are_visible_through_legacy_readers(
    db_session_factory, admin_user, member_user
):
    event = EventStore(db_session_factory).create_event(
        name="Linked event", type="organization", category="workshop", status="active"
    )
    task = TaskStore(db_session_factory).create(
        "Linked task", admin_user.jid, event_id=event["id"]
    )
    WorkStore(db_session_factory).assign("task", task.id, member_user.jid)

    assignments = EventStore(db_session_factory).get_user_assignments(member_user.jid)
    assert assignments == [{
        "event_id": event["id"],
        "event_name": "Linked event",
        "event_type": "task",
        "status": "pending",
        "target_type": "task",
        "task_id": task.id,
        "task_name": "Linked task",
    }]
    event_view = EventStore(db_session_factory).get_event(event["id"])
    assert event_view["assignment_count"] == 1
    assert event_view["task_assignment_count"] == 1


def test_task_can_be_relinked_to_another_event(db_session_factory, handler, admin_user):
    mock_client, run = handler
    first = EventStore(db_session_factory).create_event(
        name="First parent", type="organization", category="workshop", status="active"
    )
    second = EventStore(db_session_factory).create_event(
        name="Second parent", type="organization", category="workshop", status="active"
    )
    task = TaskStore(db_session_factory).create(
        "Move me", admin_user.jid, event_id=first["id"]
    )

    run(
        mock_client,
        make_msg(
            f"!update-task {task.id} | event {second['id']}",
            admin_user.jid,
        ),
    )

    with db_session_factory() as session:
        assert session.get(Task, task.id).event_id == second["id"]


def test_legacy_task_owner_update_replaces_the_previous_person(
    db_session_factory, admin_user, member_user
):
    other = upsert_user(db_session_factory, "other@s.whatsapp.net", role="member")
    task = TaskStore(db_session_factory).create("Replace owner", admin_user.jid)
    store = TaskStore(db_session_factory)
    store.update(task.id, assignee_jid=member_user.jid)
    store.update(task.id, assignee_jid=other.jid)

    rows = WorkStore(db_session_factory).overview(
        target_type="task", target_id=task.id, admin=True
    )
    assert [row["user_jid"] for row in rows] == [other.jid]
