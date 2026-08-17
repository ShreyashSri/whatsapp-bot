"""Tests for PRD FR-7 Reminder Store and Feature."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.auth import upsert_user
from db.event_store import EventStore
from db.models import Assignment, AuditLog, Base, ReminderConfig, ReminderLog, User
from db.reminder_store import ReminderStore
from features.reminders import register as register_reminders


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory


@pytest.fixture
def reminder_store(db_session_factory):
    return ReminderStore(db_session_factory)


@pytest.fixture
def admin_user(db_session_factory):
    return upsert_user(
        db_session_factory,
        "admin@s.whatsapp.net",
        role="admin",
        display_name="Admin",
    )


@pytest.fixture
def member_user(db_session_factory):
    return upsert_user(
        db_session_factory,
        "member@s.whatsapp.net",
        role="member",
        display_name="Member",
    )


def test_get_and_update_config(reminder_store, admin_user):
    cfg = reminder_store.get_config()
    assert cfg["frequency_hours"] == 24
    assert cfg["active_window_start"] == "09:00"
    assert cfg["active_window_end"] == "18:00"
    assert cfg["escalation_threshold"] == 3

    updated = reminder_store.update_config(
        actor=admin_user,
        frequency_hours=12,
        active_window_start="08:00",
        active_window_end="20:00",
        escalation_threshold=5,
        escalation_channel="admin@s.whatsapp.net",
    )
    assert updated["frequency_hours"] == 12
    assert updated["active_window_start"] == "08:00"
    assert updated["active_window_end"] == "20:00"
    assert updated["escalation_threshold"] == 5
    assert updated["escalation_channel"] == "admin@s.whatsapp.net"


def test_config_validation(reminder_store):
    with pytest.raises(ValueError, match="frequency_hours must be greater than 0"):
        reminder_store.update_config(frequency_hours=0)

    with pytest.raises(ValueError, match="active_window_start must be HH:MM format"):
        reminder_store.update_config(active_window_start="invalid")

    with pytest.raises(ValueError, match="escalation_threshold must be greater than 0"):
        reminder_store.update_config(escalation_threshold=-1)


def test_active_window_check(reminder_store):
    reminder_store.update_config(active_window_start="09:00", active_window_end="18:00")
    t_inside = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    t_outside = datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc)

    assert reminder_store.is_within_active_window(t_inside) is True
    assert reminder_store.is_within_active_window(t_outside) is False


def test_reminder_run_and_idempotency(db_session_factory, reminder_store, admin_user, member_user):
    event_store = EventStore(db_session_factory)
    evt = event_store.create_event(name="Test Org Event", type="organization")
    assignment = event_store.assign(evt["id"], member_user.jid)

    # First run should find eligible assignment and send reminder
    mock_client = MagicMock()
    res1 = reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)

    assert res1["eligible"] == 1
    assert res1["sent"] == 1
    assert res1["escalated"] == 0
    assert res1["failed"] == 0
    mock_client.send_message.assert_called_once()
    reminder_text = mock_client.send_message.call_args[0][1]
    assert "Please reply with your current status and a short note." in reminder_text
    assert f"I’ve started {evt['name']}" in reminder_text
    assert f"I completed {evt['name']}" in reminder_text
    assert "note:" in reminder_text
    assert "!work" not in reminder_text

    # Verify assignment updated
    with db_session_factory() as session:
        asgn = session.get(Assignment, assignment["id"])
        assert asgn.missed_count == 1
        assert asgn.reminder_state == "sent"

    # Second run immediately after should find 0 eligible due to idempotency & frequency cutoff
    mock_client.reset_mock()
    res2 = reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)
    assert res2["eligible"] == 0
    assert res2["sent"] == 0
    mock_client.send_message.assert_not_called()


def test_reminder_escalation(db_session_factory, reminder_store, admin_user, member_user):
    reminder_store.update_config(
        actor=admin_user,
        escalation_threshold=2,
        escalation_channel="admin@s.whatsapp.net",
    )

    event_store = EventStore(db_session_factory)
    evt = event_store.create_event(name="Escalation Event", type="organization")
    asgn_dict = event_store.assign(evt["id"], member_user.jid)

    # Set missed_count to 1 directly to simulate previous missed reminders
    with db_session_factory() as session:
        asgn = session.get(Assignment, asgn_dict["id"])
        asgn.missed_count = 1
        session.commit()

    mock_client = MagicMock()
    res = reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)

    assert res["eligible"] == 1
    assert res["sent"] == 0
    assert res["escalated"] == 1

    with db_session_factory() as session:
        asgn = session.get(Assignment, asgn_dict["id"])
        assert asgn.missed_count == 2
        assert asgn.reminder_state == "escalated"

        # Check audit log
        audit_entries = session.scalars(
            Base.metadata.tables["audit_logs"].select().where(AuditLog.operation == "reminder.run")
        ).all()
        assert len(audit_entries) >= 1


def test_escalation_delivery_failure_does_not_repeat_the_reminder(
    db_session_factory, reminder_store, admin_user, member_user
):
    reminder_store.update_config(
        actor=admin_user,
        escalation_threshold=1,
        escalation_channel="admin@s.whatsapp.net",
    )
    event_store = EventStore(db_session_factory)
    event = event_store.create_event(name="Escalation Retry", type="organization")
    assignment = event_store.assign(event["id"], member_user.jid)

    client = MagicMock()
    client.send_message.side_effect = [None, RuntimeError("channel unavailable")]
    result = reminder_store.run_reminders(client, admin_user, force_ignore_window=True)

    assert result["sent"] == 1
    assert result["escalated"] == 0
    assert reminder_store.run_reminders(client, admin_user, force_ignore_window=True)["eligible"] == 0
    history = reminder_store.get_history(assignment["id"])
    assert "escalation delivery unavailable" in history[0]["details"]


def test_reminder_history(db_session_factory, reminder_store, admin_user, member_user):
    event_store = EventStore(db_session_factory)
    evt = event_store.create_event(name="History Event", type="organization")
    asgn_dict = event_store.assign(evt["id"], member_user.jid)

    mock_client = MagicMock()
    reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)

    history = reminder_store.get_history(asgn_dict["id"])
    assert len(history) == 1
    assert history[0]["assignment_id"] == asgn_dict["id"]
    assert history[0]["result"] == "sent"


def test_reminder_feature_commands(db_session_factory, admin_user, member_user):
    mock_client = MagicMock()
    config = {"db_session_factory": db_session_factory}
    handler = register_reminders(mock_client, config)

    # Helper to construct mock MessageEv
    def make_msg(text, sender_jid):
        msg = MagicMock()
        msg.Info.MessageSource.Chat.Server = "g.us"
        msg.Info.MessageSource.Sender = sender_jid
        msg.Message.conversation = text
        msg.Message.extendedTextMessage = None
        msg.Message.imageMessage = None
        return msg

    # Non-admin trying !reminder-config should be denied by auth gate
    mock_client.reset_mock()
    handler(mock_client, make_msg("!reminder-config frequency: 12", member_user.jid))
    mock_client.send_message.assert_called_with(
        mock_client.send_message.call_args[0][0],
        "⛔ You need to be an active administrator to use this command.",
    )

    # Admin running !reminder-config
    mock_client.reset_mock()
    handler(mock_client, make_msg("!reminder-config frequency: 12", admin_user.jid))
    call_args = mock_client.send_message.call_args[0][1]
    assert "✅ Reminder config updated!" in call_args
    assert "12h" in call_args

    # Member running !reminders summary
    mock_client.reset_mock()
    handler(mock_client, make_msg("!reminders", member_user.jid))
    call_args = mock_client.send_message.call_args[0][1]
    assert "⏰ *Reminder System Status*" in call_args

    # Admin running !reminder-run
    mock_client.reset_mock()
    handler(mock_client, make_msg("!reminder-run", admin_user.jid))
    call_args = mock_client.send_message.call_args[0][1]
    assert "⚡ *Reminder Run Completed*" in call_args


def test_task_reminders(db_session_factory, reminder_store, admin_user, member_user):
    from db.models import Task, Assignment
    from datetime import datetime, timezone
    
    # Create a task in DB
    now = datetime.now(timezone.utc)
    with db_session_factory() as session:
        task = Task(title="Test Task Title", description="Test Desc", due_date=None, priority="medium", status="todo", created_by_jid=admin_user.jid, created_at=now, updated_at=now)
        session.add(task)
        session.commit()
        task_id = task.id
    
    # Assign the task using WorkStore
    from db.work_store import WorkStore
    ws = WorkStore(db_session_factory)
    assignment = ws.assign("task", task_id, member_user.jid)
    
    # Run reminders
    mock_client = MagicMock()
    res = reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)
    
    assert res["eligible"] == 1
    assert res["sent"] == 1
    
    # Verify assignment has 1 missed count and reminder_state="sent"
    with db_session_factory() as session:
        asgn = session.get(Assignment, assignment["id"])
        assert asgn.missed_count == 1
        assert asgn.reminder_state == "sent"
        
    # Verify sent message content contains "task" and "Test Task Title"
    mock_client.send_message.assert_called_once()
    msg_text = mock_client.send_message.call_args[0][1]
    assert "task" in msg_text
    assert "Test Task Title" in msg_text


def test_reminder_resets_on_updates(db_session_factory, admin_user, member_user):
    from db.models import Task, Assignment
    from updates.operations import submit_update, edit_update
    from db.work_store import WorkStore
    from datetime import datetime, timezone
    from sqlalchemy import select
    
    now = datetime.now(timezone.utc)
    with db_session_factory() as session:
        task = Task(title="Reset Task", status="todo", created_by_jid=admin_user.jid, created_at=now, updated_at=now)
        session.add(task)
        session.commit()
        task_id = task.id

    ws = WorkStore(db_session_factory)
    asgn_dict = ws.assign("task", task_id, member_user.jid)
    asgn_id = asgn_dict["id"]
    
    # Simulate missed reminders
    with db_session_factory() as session:
        asgn = session.get(Assignment, asgn_id)
        asgn.missed_count = 2
        asgn.reminder_state = "sent"
        session.commit()

    # Submit an update via updates.operations
    with db_session_factory() as session:
        submit_update(session, str(asgn_id), "note", "working on it", member_user.jid)
        
    with db_session_factory() as session:
        asgn = session.get(Assignment, asgn_id)
        assert asgn.missed_count == 0
        assert asgn.reminder_state is None
        
    # Test edit_update as well
    with db_session_factory() as session:
        asgn.missed_count = 3
        asgn.reminder_state = "escalated"
        session.commit()
        
    # Edit update
    with db_session_factory() as session:
        # Get revision id
        from db.models import ProgressRevision
        rev = session.scalar(select(ProgressRevision).where(ProgressRevision.assignment_id == asgn_id))
        edit_update(session, str(rev.id), "working hard", member_user.jid)
        
    with db_session_factory() as session:
        asgn = session.get(Assignment, asgn_id)
        assert asgn.missed_count == 0
        assert asgn.reminder_state is None

    # Test work store direct submit_update resets too
    with db_session_factory() as session:
        asgn.missed_count = 4
        asgn.reminder_state = "sent"
        session.commit()
        
    ws.submit_update(str(asgn_id), "note", "final updates", member_user.jid)
    with db_session_factory() as session:
        asgn = session.get(Assignment, asgn_id)
        assert asgn.missed_count == 0
        assert asgn.reminder_state is None


def test_unified_reminders_config(db_session_factory, admin_user):
    mock_client = MagicMock()
    config = {"db_session_factory": db_session_factory}
    handler = register_reminders(mock_client, config)

    # Helper to construct mock MessageEv
    def make_msg(text, sender_jid):
        msg = MagicMock()
        msg.Info.MessageSource.Chat.Server = "g.us"
        msg.Info.MessageSource.Sender = sender_jid
        msg.Message.conversation = text
        msg.Message.extendedTextMessage = None
        msg.Message.imageMessage = None
        return msg

    # Admin running !reminders config frequency 6
    mock_client.reset_mock()
    handler(mock_client, make_msg("!reminders config frequency 6", admin_user.jid))
    call_args = mock_client.send_message.call_args[0][1]
    assert "✅ Reminder config updated!" in call_args
    assert "6h" in call_args


def test_reminders_stop_when_event_is_closed(db_session_factory, reminder_store, admin_user, member_user):
    """Reminders run daily until the work is closed or deleted."""
    event_store = EventStore(db_session_factory)
    for lifecycle in ("completed", "cancelled"):
        evt = event_store.create_event(name=f"Event {lifecycle}", type="organization", status="active")
        event_store.assign(evt["id"], member_user.jid)
        event_store.set_status(evt["id"], lifecycle)
        mock_client = MagicMock()
        result = reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)
        assert result["eligible"] == 0, f"{lifecycle} event still reminded"
        mock_client.send_message.assert_not_called()


def test_reminders_stop_when_event_is_deleted(db_session_factory, reminder_store, admin_user, member_user):
    event_store = EventStore(db_session_factory)
    evt = event_store.create_event(name="Deleted Event", type="organization", status="active")
    event_store.assign(evt["id"], member_user.jid)
    event_store.delete_event(evt["id"])
    mock_client = MagicMock()
    assert reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)["eligible"] == 0
    mock_client.send_message.assert_not_called()


def test_reminders_stop_when_task_is_closed(db_session_factory, reminder_store, admin_user, member_user):
    from db.task_store import TaskStore
    from db.work_store import WorkStore
    for lifecycle in ("done", "cancelled"):
        task = TaskStore(db_session_factory).create(f"Task {lifecycle}", admin_user.jid)
        WorkStore(db_session_factory).assign("task", task.id, member_user.jid)
        TaskStore(db_session_factory).update(task.id, status=lifecycle, force_status=True)
        mock_client = MagicMock()
        result = reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)
        assert result["eligible"] == 0, f"{lifecycle} task still reminded"


def test_active_work_still_reminds(db_session_factory, reminder_store, admin_user, member_user):
    event_store = EventStore(db_session_factory)
    evt = event_store.create_event(name="Open Event", type="organization", status="active")
    event_store.assign(evt["id"], member_user.jid)
    mock_client = MagicMock()
    assert reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)["sent"] == 1


def test_default_cadence_is_daily(reminder_store):
    assert reminder_store.get_config()["frequency_hours"] == 24


def test_reminder_is_delivered_to_the_member_dm(db_session_factory, reminder_store, admin_user, member_user):
    event_store = EventStore(db_session_factory)
    evt = event_store.create_event(name="DM Event", type="organization", status="active")
    event_store.assign(evt["id"], member_user.jid)
    mock_client = MagicMock()
    reminder_store.run_reminders(mock_client, admin_user, force_ignore_window=True)
    recipient = mock_client.send_message.call_args[0][0]
    assert getattr(recipient, "Server", "") == "s.whatsapp.net"
    assert member_user.jid.split("@")[0] in str(getattr(recipient, "User", ""))


def test_reminders_for_one_person_are_batched(db_session_factory, reminder_store, admin_user, member_user):
    event_store = EventStore(db_session_factory)
    first = event_store.create_event(name="First item", type="organization", status="active")
    second = event_store.create_event(name="Second item", type="organization", status="active")
    event_store.assign(first["id"], member_user.jid)
    event_store.assign(second["id"], member_user.jid)

    client = MagicMock()
    result = reminder_store.run_reminders(client, admin_user, force_ignore_window=True)

    assert result["eligible"] == 2
    assert result["sent"] == 2
    client.send_message.assert_called_once()
    message = client.send_message.call_args[0][1]
    assert "First item" in message and "Second item" in message
    assert len(reminder_store.get_history(limit=10)) == 2


def test_three_assignees_on_task_use_configured_group(db_session_factory, reminder_store, admin_user, member_user):
    from db.task_store import TaskStore
    from db.work_store import WorkStore

    other = upsert_user(db_session_factory, "other@s.whatsapp.net", role="member")
    third = upsert_user(db_session_factory, "third@s.whatsapp.net", role="member")
    task = TaskStore(db_session_factory).create("Team task", admin_user.jid)
    work = WorkStore(db_session_factory)
    work.assign_many("task", task.id, [member_user.jid, other.jid, third.jid])

    client = MagicMock()
    result = reminder_store.run_reminders(
        client,
        admin_user,
        force_ignore_window=True,
        group_jid="12345@g.us",
    )

    assert result["eligible"] == 3
    assert result["sent"] == 3
    client.send_message.assert_called_once()
    recipient = client.send_message.call_args[0][0]
    assert getattr(recipient, "Server", "") == "g.us"
    assert "Team task" in client.send_message.call_args[0][1]
    assert all(item["channel"] == "whatsapp-group" for item in reminder_store.get_history(limit=10))


def test_reminder_batch_bookkeeping_is_atomic_across_the_whole_batch(
    db_session_factory, reminder_store, admin_user, member_user, monkeypatch
):
    """One WhatsApp message covers an entire batch, so a crash partway
    through recording delivery for that batch must not leave some
    assignments marked delivered and others not -- that would silently
    re-remind people who already received the message and delay escalation
    for the ones that were skipped."""
    from db.task_store import TaskStore
    from db.work_store import WorkStore
    import features.text as text_module

    other = upsert_user(db_session_factory, "other@s.whatsapp.net", role="member")
    third = upsert_user(db_session_factory, "third@s.whatsapp.net", role="member")
    task = TaskStore(db_session_factory).create("Batch task", admin_user.jid)
    work = WorkStore(db_session_factory)
    work.assign_many("task", task.id, [member_user.jid, other.jid, third.jid])

    # 3 calls build the batched message text (one per item), then the
    # bookkeeping loop calls it again per item before writing that item's
    # ReminderLog/Assignment update -- the 5th call is the second item's
    # bookkeeping write, i.e. mid-batch.
    real_public_text = text_module.public_text
    calls = {"n": 0}

    def flaky_public_text(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("simulated mid-batch failure")
        return real_public_text(*args, **kwargs)

    monkeypatch.setattr(text_module, "public_text", flaky_public_text)
    client = MagicMock()

    with pytest.raises(RuntimeError, match="simulated mid-batch failure"):
        reminder_store.run_reminders(
            client, admin_user, force_ignore_window=True, group_jid="12345@g.us",
        )

    with db_session_factory() as session:
        rows = session.query(Assignment).filter_by(task_id=task.id).all()
        assert len(rows) == 3
        assert all(row.missed_count == 0 for row in rows)
        assert all(row.reminder_state is None for row in rows)
    assert reminder_store.get_history(limit=10) == []


def test_on_demand_reminder_bypasses_frequency_but_keeps_member_scope(
    db_session_factory, reminder_store, admin_user, member_user
):
    event_store = EventStore(db_session_factory)
    event = event_store.create_event(name="Immediate item", type="organization", status="active")
    event_store.assign(event["id"], member_user.jid)
    client = MagicMock()

    result = reminder_store.run_reminders(
        client,
        member_user,
        force_ignore_window=True,
        target_type="event",
        target_id=event["id"],
        user_jid=member_user.jid,
        ignore_idempotency=True,
    )

    assert result["eligible"] == 1
    assert result["sent"] == 1
    assert getattr(client.send_message.call_args[0][0], "Server", "") == "s.whatsapp.net"
