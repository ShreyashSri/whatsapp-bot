import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock, patch
from flask import Flask

from db.auth import authorize, upsert_user
from db.event_store import EventStore
from db.models import Assignment, Base, EventFieldSchema, Task
from db.reminder_store import ReminderStore
from db.task_store import TaskStore
from db.work_store import WorkStore
from features.incidents import _build_chat_jid, register as register_incidents
from features.natural_language import (
    compile_intent,
    _intent_compile_error,
    _named_entity_candidates,
    _resolve_target_reference,
)


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:")

    @sqlalchemy_event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_authorize_accepts_push_name(factory):
    user = authorize(
        factory,
        "new-user@s.whatsapp.net",
        "test.identify",
        push_name="New User",
    )

    assert user is not None
    assert user.display_name == "New User"


def test_delete_event_keeps_dependent_schema(factory):
    event_store = EventStore(factory)
    event = event_store.create_event(name="Schema event", type="participation")
    with factory.begin() as session:
        session.add(EventFieldSchema(
            event_id=event["id"],
            name="org",
            field_type="text",
            position=0,
        ))

    assert event_store.delete_event(event["id"]) is True

    with factory() as session:
        fields = session.query(EventFieldSchema).filter_by(event_id=event["id"]).all()
        assert len(fields) == 1


def test_work_store_rejects_cross_user_progress(factory):
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")
    attacker = upsert_user(factory, "attacker@s.whatsapp.net", display_name="Attacker")
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin", display_name="Admin")
    event_store = EventStore(factory)
    event = event_store.create_event(name="Work event", type="participation")
    event_store.assign(event["id"], owner.jid)
    work_store = WorkStore(factory)
    reference = f"event {event['id']}@{owner.jid}"
    revision = work_store.submit_update(reference, "note", "original", owner.jid)

    with pytest.raises(ValueError, match="own assignment"):
        work_store.submit_update(reference, "note", "hijacked", attacker.jid)
    with pytest.raises(ValueError, match="own assignment"):
        work_store.edit_update(revision["id"], "hijacked", attacker.jid)

    edited = work_store.edit_update(revision["id"], "corrected", admin.jid, admin=True)
    assert edited["value"] == "corrected"


def test_named_work_target_ties_fail_closed_instead_of_picking_first_task(factory):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    tasks = TaskStore(factory)
    for title in ("pr1", "pr2", "pr3", "pr4"):
        tasks.create(title, admin.jid)

    assert _resolve_target_reference(
        factory,
        {"target_type": "task", "target_name": "pr merged"},
    ) is None
    assert _resolve_target_reference(
        factory,
        {"target_type": "task", "target_name": "pr 3"},
    ) == "task 3"
    tasks.create("alpha", admin.jid)
    tasks.create("alphi", admin.jid)
    assert _named_entity_candidates(factory, "alp") == []
    assert _intent_compile_error(
        {
            "capability": "work.update",
            "arguments": {
                "target_type": "task",
                "field": "status",
                "value": "completed",
            },
        }
    ) == "work.update requires argument target"
    note_only_target = {
        "capability": "work.update",
        "arguments": {
            "target_type": "task",
            "target_name": "pr merged",
            "field": "status",
            "value": "completed",
        },
    }
    assert _intent_compile_error(
        note_only_target,
        "update status to completed, note pr merged",
    ) == "work.update requires argument target"
    assert compile_intent(
        note_only_target,
        "update status to completed, note pr merged",
        factory,
        [],
        allow_text_target_fallback=False,
    ) is None


def test_task_assignment_link_is_canonical_and_clears_legacy_owner(factory):
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin", display_name="Admin")
    task = TaskStore(factory).create("Canonical task", admin.jid)

    with factory.begin() as session:
        session.get(Task, task.id).assignee_jid = owner.jid

    work_store = WorkStore(factory)
    work_store.assign("task", task.id, owner.jid)
    with factory() as session:
        assert session.get(Task, task.id).assignee_jid is None

    work_store.unassign("task", task.id, owner.jid)
    with factory() as session:
        assert session.get(Task, task.id).assignee_jid is None

    TaskStore(factory).update(task.id, status="done", force_status=True)
    work_store.assign("task", task.id, owner.jid)
    with factory() as session:
        assignment = session.query(Assignment).filter_by(task_id=task.id).one()
        assert assignment.status == "completed"


def test_failed_reminder_does_not_mark_assignment_delivered(factory):
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin", display_name="Admin")
    event = EventStore(factory).create_event(name="Reminder event", type="participation")
    EventStore(factory).assign(event["id"], owner.jid)

    result = ReminderStore(factory).run_reminders(
        client=None,
        actor=admin,
        force_ignore_window=True,
    )

    assert result["failed"] == 1
    with factory() as session:
        assignment = session.query(Assignment).one()
        assert assignment.missed_count == 0
        assert assignment.reminder_state is None


def test_incident_group_jid_preserves_group_server():
    jid = _build_chat_jid("123456@g.us")

    assert jid.User == "123456"
    assert jid.Server == "g.us"


def test_incident_webhook_requires_secret_and_sends_to_group(factory):
    client = MagicMock()
    captured = {}

    class NoStartThread:
        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            return None

    def capture_app(*args, **kwargs):
        captured["app"] = Flask(*args, **kwargs)
        return captured["app"]

    with patch("features.incidents.Flask", side_effect=capture_app), \
         patch("features.incidents.threading.Thread", NoStartThread):
        register_incidents(client, {
            "db_session_factory": factory,
            "incident_group_id": "123456@g.us",
            "incident_webhook_secret": "secret",
            "incident_port": 0,
        })

    app = captured["app"]
    payload = {
        "data": [{
            "metric": {"instance": "https://example.com"},
            "value": [0, 500],
        }],
    }
    with app.test_client() as test_client:
        assert test_client.post("/alert", json=payload).status_code == 401
        response = test_client.post(
            "/alert",
            json=payload,
            headers={"X-Incident-Webhook-Secret": "secret"},
        )

    assert response.status_code == 200
    sent_jid = client.send_message.call_args.args[0]
    assert sent_jid.User == "123456"
    assert sent_jid.Server == "g.us"
