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
from db.models import Base
from features.work import register as register_work


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


def make_msg(text: str, sender_jid: str):
    msg = MagicMock()
    msg.Info.MessageSource.Chat.Server = "g.us"
    msg.Info.MessageSource.Sender = sender_jid
    msg.Message.conversation = text
    msg.Message.extendedTextMessage = None
    msg.Message.imageMessage = None
    return msg


def last_reply(mock_client) -> str:
    return mock_client.send_message.call_args[0][1]


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
