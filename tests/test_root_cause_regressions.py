from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock, patch
from flask import Flask

from db.auth import authorize, canonical_jid, current_user, known_lid_for, upsert_user
from db.event_store import EventStore
from db.models import Assignment, Base, EventFieldSchema, ProgressRevision, ReminderLog, Task
from db.reminder_store import ReminderStore
from db.task_store import TaskStore
from db.work_store import WorkStore
from db.work_store import _JID_ALIASES
from features.incidents import _build_chat_jid, register as register_incidents
from features.natural_language import (
    compile_intent,
    _intent_compile_error,
    _named_entity_candidates,
    _resolve_target_reference,
    _target_arguments,
)
from db.subgroup_store import SubgroupStore
from features.subgroups import _cmd_subgroup_info, _resolve_lid_to_pn


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


def test_fresh_lid_is_resolved_synchronously_before_target_resolution():
    from neonize.utils import build_jid

    client = MagicMock()
    client.get_pn_from_lid.return_value = build_jid("919999999999", "s.whatsapp.net")

    assert _resolve_lid_to_pn(client, "12345@lid") == "919999999999@s.whatsapp.net"
    client.get_pn_from_lid.assert_called_once()


def test_canonical_jid_bridges_lid_to_known_phone_alias(monkeypatch):
    monkeypatch.setitem(_JID_ALIASES, "256023117971610", "919606214389")

    assert canonical_jid("256023117971610@lid") == "919606214389@s.whatsapp.net"
    # A plain phone JID with no alias entry passes through unchanged.
    assert canonical_jid("919606214389@s.whatsapp.net") == "919606214389@s.whatsapp.net"
    # An unknown LID also passes through unchanged rather than raising.
    assert canonical_jid("000000000000000@lid") == "000000000000000@lid"


def test_known_lid_for_reverses_the_same_alias(monkeypatch):
    monkeypatch.setitem(_JID_ALIASES, "256023117971610", "919606214389")

    assert known_lid_for("919606214389@s.whatsapp.net") == "256023117971610@lid"
    assert known_lid_for("000000000000000@s.whatsapp.net") == "000000000000000@s.whatsapp.net"


def test_lid_auth_uses_canonical_phone_user_when_both_rows_exist(factory, monkeypatch):
    phone_user = upsert_user(
        factory, "919606214389@s.whatsapp.net", role="admin", display_name="Admin"
    )
    upsert_user(factory, "256023117971610@lid", role="member", display_name="LID")
    monkeypatch.setitem(_JID_ALIASES, "256023117971610", "919606214389")

    resolved = current_user(factory, "256023117971610@lid")

    assert resolved is not None
    assert resolved.jid == phone_user.jid
    assert resolved.role == "admin"


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


def test_delete_event_soft_deletes_child_tasks_but_keeps_history(factory):
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")
    events = EventStore(factory)
    event = events.create_event(name="Parent event", type="organization")
    task = TaskStore(factory).create("Child task", owner.jid, event_id=event["id"])
    assignment = WorkStore(factory).assign("task", task.id, owner.jid)
    now = datetime.now(timezone.utc)

    with factory.begin() as session:
        session.add(ProgressRevision(
            assignment_id=assignment["id"],
            field="note",
            value="child progress",
            author_jid=owner.jid,
            timestamp=now,
        ))
        session.add(ReminderLog(
            assignment_id=assignment["id"],
            timestamp=now,
            channel="whatsapp",
            result="sent",
            details="child reminder",
        ))

    assert events.delete_event(event["id"]) is True
    assert events.get_event(event["id"]) is None
    assert TaskStore(factory).get(task.id) is None
    assert TaskStore(factory).list_all() == []
    with factory() as session:
        stored_task = session.get(Task, task.id)
        assert stored_task.deleted_at is not None
        assert session.query(Assignment).filter_by(task_id=task.id).count() == 1
        assert session.query(ProgressRevision).one().assignment_id == assignment["id"]
        assert session.query(ReminderLog).one().assignment_id == assignment["id"]


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

    named_task = tasks.create("tell result", admin.jid)
    assert _resolve_target_reference(
        factory,
        _target_arguments(
            {"target_type": "task", "target_name": "task"},
            "assign task tell result to @Shuvam",
        ),
    ) == f"task {named_task.id}"
    assert _resolve_target_reference(
        factory,
        _target_arguments(
            {"target_type": "task", "target_name": str(named_task.id)},
            f"assign task {named_task.id} to @Shuvam",
        ),
    ) == f"task {named_task.id}"

    bare_task = tasks.create("fuck off", admin.jid)
    assert _resolve_target_reference(
        factory,
        _target_arguments(
            {"target_id": bare_task.id},
            "assign fuck off to @Bibisha",
        ),
    ) == f"task {bare_task.id}"


def test_structured_numeric_task_target_honors_parent_event_scope(factory):
    admin = upsert_user(factory, "scope-admin@s.whatsapp.net", role="admin")
    events = EventStore(factory)
    first = events.create_event(name="first", type="organization", status="active")
    events.create_event(name="second", type="organization", status="active")
    task = TaskStore(factory).create("shared", admin.jid, event_id=first["id"])

    assert _resolve_target_reference(
        factory,
        {
            "target": {"type": "task", "id": task.id},
            "parent_event_name": "first",
        },
    ) == f"task {task.id}"
    assert _resolve_target_reference(
        factory,
        {
            "target": {"type": "task", "id": task.id},
            "parent_event_name": "second",
        },
    ) is None
    assert _resolve_target_reference(
        factory,
        {"target_type": "event", "task_id": task.id},
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


def test_resolve_in_fails_closed_on_multiple_assignees_without_jid(factory):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    first = upsert_user(factory, "first@s.whatsapp.net", display_name="First")
    second = upsert_user(factory, "second@s.whatsapp.net", display_name="Second")
    task = TaskStore(factory).create("Shared task", admin.jid)

    work_store = WorkStore(factory)
    work_store.assign_many("task", task.id, [first.jid, second.jid])

    # A bare "task <id>" reference with no assignee JID must never silently
    # pick a row by insertion order -- it has to name the ambiguity instead.
    with pytest.raises(ValueError, match="multiple assignees"):
        work_store.resolve(f"task:{task.id}")
    with pytest.raises(ValueError, match="multiple assignees"):
        work_store.set_status(f"task:{task.id}", "in_progress", admin.jid, admin=True)

    # The same reference with an explicit assignee still resolves cleanly.
    resolved = work_store.resolve(f"task:{task.id}@{first.jid}")
    assert resolved.user_jid == first.jid


def test_resolve_in_returns_single_assignee_without_jid(factory):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")
    task = TaskStore(factory).create("Solo task", admin.jid)

    work_store = WorkStore(factory)
    work_store.assign("task", task.id, owner.jid)

    resolved = work_store.resolve(f"task:{task.id}")
    assert resolved.user_jid == owner.jid


def test_assign_reuses_canonical_phone_user_for_known_lid_alias(factory, monkeypatch):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    phone_user = upsert_user(
        factory, "919606214389@s.whatsapp.net", display_name="Phone"
    )
    task = TaskStore(factory).create("Aliased task", admin.jid)
    monkeypatch.setitem(_JID_ALIASES, "256023117971610", "919606214389")

    work_store = WorkStore(factory)
    row = work_store.assign("task", task.id, "256023117971610@lid")

    # Assigning by the LID must land on the already-known phone account,
    # not mint a second, phantom User row keyed by the LID.
    assert row["user_jid"] == phone_user.jid
    with factory() as session:
        assert session.get(Task, task.id) is not None
        from db.models import User

        assert session.get(User, "256023117971610@lid") is None


def test_task_create_is_atomic_with_its_initial_assignment(factory, monkeypatch):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")

    def _boom(self, session, target_type, target_id, user_jid):
        raise RuntimeError("simulated assignment failure")

    monkeypatch.setattr(WorkStore, "_assign_in", _boom)

    with pytest.raises(RuntimeError, match="simulated assignment failure"):
        TaskStore(factory).create("Atomic task", admin.jid, assignee_jid=owner.jid)

    # A failure partway through must never leave an orphaned, unassigned
    # task behind -- task creation and its initial assignment are one
    # transaction, not two.
    with factory() as session:
        assert session.query(Task).filter_by(title="Atomic task").first() is None


def test_task_create_can_assign_multiple_people_atomically(factory, monkeypatch):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    bob = upsert_user(factory, "bob@s.whatsapp.net", display_name="Bob")
    carol = upsert_user(factory, "carol@s.whatsapp.net", display_name="Carol")

    task = TaskStore(factory).create(
        "Team task", admin.jid, assignee_jids=[bob.jid, carol.jid, bob.jid],
    )
    with factory() as session:
        rows = session.query(Assignment).filter_by(task_id=task.id).all()
        assert {row.user_jid for row in rows} == {bob.jid, carol.jid}

    # And it stays atomic across several assignees the same way it does for
    # one: a failure partway through leaves no task and no partial rows.
    calls = {"n": 0}
    real_assign_in = WorkStore._assign_in

    def _boom_on_second(self, session, target_type, target_id, user_jid):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated assignment failure")
        return real_assign_in(self, session, target_type, target_id, user_jid)

    monkeypatch.setattr(WorkStore, "_assign_in", _boom_on_second)
    with pytest.raises(RuntimeError, match="simulated assignment failure"):
        TaskStore(factory).create(
            "Should not exist", admin.jid, assignee_jids=[bob.jid, carol.jid],
        )
    with factory() as session:
        assert session.query(Task).filter_by(title="Should not exist").first() is None


def test_task_lifecycle_sync_reverts_assignment_status_on_failure(factory, monkeypatch):
    from features.work import _sync_task_lifecycle_or_revert

    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    alice = upsert_user(factory, "alice@s.whatsapp.net", display_name="Alice")
    store = WorkStore(factory)
    task = TaskStore(factory).create("Write report", admin.jid, assignee_jid=alice.jid)
    reference = f"task:{task.id}@{alice.jid}"

    result = store.set_status(reference, "completed", alice.jid)
    assert result["previous_status"] == "pending"

    def _boom(self, *_args, **_kwargs):
        raise ValueError("simulated task-lifecycle failure")

    monkeypatch.setattr(TaskStore, "update", _boom)

    with pytest.raises(ValueError, match="simulated task-lifecycle failure"):
        _sync_task_lifecycle_or_revert(factory, store, reference, result, task.id, "done")

    # The assignment write must not be left stuck on "completed" once the
    # paired task-lifecycle write failed -- both sides of a status update
    # must agree, or neither should change.
    rows = store.overview(target_type="task", target_id=task.id, admin=True)
    assert rows[0]["status"] == "pending"
    assert TaskStore(factory).get(task.id).status == "todo"


def test_task_complete_allows_admin_who_is_not_assigned(factory):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    alice = upsert_user(factory, "alice@s.whatsapp.net", display_name="Alice")
    task = TaskStore(factory).create("Ship it", admin.jid, assignee_jid=alice.jid)

    with pytest.raises(ValueError, match="only the assignee or an admin"):
        TaskStore(factory).complete(task.id, admin.jid)

    completed = TaskStore(factory).complete(task.id, admin.jid, admin=True)
    assert completed.status == "done"


def test_claim_message_release_allows_retry_after_dispatch_failure(factory):
    from db.nl_state import claim_message, release_message

    assert claim_message(factory, "msg-1", "alice@s.whatsapp.net", "group@g.us") is True
    assert claim_message(factory, "msg-1", "alice@s.whatsapp.net", "group@g.us") is False

    release_message(factory, "msg-1", "group@g.us")

    # Once released, the same message ID can be claimed (and thus retried)
    # again instead of being permanently dropped.
    assert claim_message(factory, "msg-1", "alice@s.whatsapp.net", "group@g.us") is True


def test_reply_message_destination_check_honors_to_and_reply_privately():
    from features.neonize_policy import _destinations

    class _Source:
        Chat = "group@g.us"
        Sender = "alice@s.whatsapp.net"

    class _Info:
        MessageSource = _Source()

    class _Quoted:
        Info = _Info()

    quoted = _Quoted()

    # Default: validated against the quoted message's chat.
    assert _destinations("reply_message", ("hi", quoted), {}) == ["group@g.us"]

    # An explicit `to=` must be validated, not the quoted message's chat --
    # otherwise a caller could redirect the send to an unauthorized JID while
    # the guard checks the (harmless) quoted chat instead.
    assert _destinations(
        "reply_message", ("hi", quoted), {"to": "outsider@s.whatsapp.net"}
    ) == ["outsider@s.whatsapp.net"]

    # reply_privately=True actually sends to the quoted message's sender, not
    # the group chat -- the guard must check that real destination.
    assert _destinations(
        "reply_message", ("hi", quoted), {"reply_privately": True}
    ) == ["alice@s.whatsapp.net"]


def test_unassign_detaches_history_instead_of_failing_on_foreign_keys(factory):
    owner = upsert_user(factory, "owner@s.whatsapp.net", display_name="Owner")
    event = EventStore(factory).create_event(name="History event", type="participation")
    assignment = EventStore(factory).assign(event["id"], owner.jid)
    now = datetime.now(timezone.utc)

    with factory.begin() as session:
        session.add(ProgressRevision(
            assignment_id=assignment["id"],
            field="note",
            value="started",
            author_jid=owner.jid,
            timestamp=now,
        ))
        session.add(ReminderLog(
            assignment_id=assignment["id"],
            timestamp=now,
            channel="whatsapp",
            result="sent",
            details="test",
        ))

    assert WorkStore(factory).unassign("event", event["id"], owner.jid)
    with factory() as session:
        assert session.query(Assignment).filter_by(event_id=event["id"]).all() == []
        assert session.query(ReminderLog).one().assignment_id is None
        assert session.query(ProgressRevision).one().assignment_id is None


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


def test_incident_alert_is_not_suppressed_after_a_failed_send(factory):
    """A transient WhatsApp send failure must not mark the incident as
    already-alerted -- otherwise the next poll sees it as known and never
    retries, silently dropping a real outage notification."""
    client = MagicMock()
    client.send_message.side_effect = [RuntimeError("transient send failure"), None]
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

    payload = {
        "data": [{
            "metric": {"instance": "https://example.com"},
            "value": [0, 500],
        }],
    }
    headers = {"X-Incident-Webhook-Secret": "secret"}
    with captured["app"].test_client() as test_client:
        first = test_client.post("/alert", json=payload, headers=headers)
        assert first.status_code == 500

        second = test_client.post("/alert", json=payload, headers=headers)
        assert second.status_code == 200

    # The alert must have actually gone out on the retry, not been treated
    # as already-seen because the first (failed) attempt marked it sent.
    assert client.send_message.call_count == 2


def test_subgroup_info_gives_lid_members_distinct_labels_not_generic_placeholder(factory):
    store = SubgroupStore(factory)
    store.write({"hey-there": ["50990036295744@lid", "263883377905788@lid"]})

    client = MagicMock()
    client.get_group_info.side_effect = Exception("no group info in tests")

    _cmd_subgroup_info(client, "group@g.us", "hey-there", store)

    sent = client.send_message.call_args[0][1]
    text = sent.extendedTextMessage.text

    # Previously every @lid member rendered as the identical, indistinguishable
    # literal placeholder "@member" -- two different people were unreadably
    # the same label. Each member must now get their own distinct token.
    assert text.count("@member") == 0
    assert "50990036295744" in text
    assert "263883377905788" in text


def test_system_prompt_disambiguates_subgroup_assignment_from_tagging():
    from features.natural_language import SYSTEM_PROMPT

    # "assign task X to subgroup Y" was previously falling through to the
    # broad collections.tag guidance (any phrasing that names a subgroup),
    # so it silently sent a broadcast tag instead of actually assigning the
    # task. The prompt must explicitly carve out subgroup assignees as
    # work.assign, not just @person assignees. Collapse whitespace since the
    # source wraps this guidance across multiple lines.
    collapsed = " ".join(SYSTEM_PROMPT.split())
    assert "assign task X to subgroup Y" in collapsed
    assert "is still work.assign" in collapsed


def test_bare_numeric_target_resolves_by_existence_when_type_is_unstated(factory):
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    task = TaskStore(factory).create("Solo numeric target", admin.jid)

    # The model sometimes emits {"target": <id>} with no target_type at all.
    # A bare int used to leave target_type empty and fall through to the
    # name-based fuzzy path (which never matches a pure number), silently
    # returning None even though the ID unambiguously names one real task.
    assert _resolve_target_reference(factory, {"target": task.id}) == f"task {task.id}"
    assert _resolve_target_reference(factory, {"target": str(task.id)}) == f"task {task.id}"

    # An ID that matches nothing on either side must still fail closed.
    assert _resolve_target_reference(factory, {"target": task.id + 999}) is None


def test_bare_numeric_target_stays_ambiguous_when_both_types_share_the_id(factory):
    from db.event_store import EventStore

    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    event = EventStore(factory).create_event(name="e1", type="organization", status="active")
    # Force the task table's autoincrement to collide with the event's ID so
    # a bare numeric target is genuinely ambiguous between the two tables.
    with factory() as session:
        from datetime import datetime, timezone
        from db.models import Task
        now = datetime.now(timezone.utc)
        session.add(Task(
            id=event["id"], title="collides", created_by_jid=admin.jid,
            created_at=now, updated_at=now,
        ))
        session.commit()

    assert _resolve_target_reference(factory, {"target": event["id"]}) is None


def test_unassign_with_unresolvable_target_fails_closed_instead_of_wiping_everything(factory):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from features.nl_operations import execute_work_assignment

    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    first = upsert_user(factory, "first@s.whatsapp.net", display_name="First")
    second = upsert_user(factory, "second@s.whatsapp.net", display_name="Second")
    task_a = TaskStore(factory).create("Task A", admin.jid)
    task_b = TaskStore(factory).create("Task B", admin.jid)
    work_store = WorkStore(factory)
    work_store.assign("task", task_a.id, first.jid)
    work_store.assign("task", task_b.id, second.jid)

    client = MagicMock()
    message = SimpleNamespace(
        Info=SimpleNamespace(
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender=admin.jid,
            )
        )
    )
    # A target that names neither an existing task nor event -- this is
    # exactly the shape that used to be treated as "no target given" and
    # silently unassigned every work item in the workspace instead of
    # failing with a clear error.
    intent = {"capability": "work.unassign", "arguments": {"target": 99999}}
    result = execute_work_assignment(
        client, message, intent, [first.jid, second.jid], factory,
        lambda arguments: _resolve_target_reference(factory, arguments),
    )

    assert result is None
    reply = client.send_message.call_args[0][1]
    assert "Removed" not in reply
    assert "across all work items" not in reply

    remaining = WorkStore(factory).overview(admin=True)
    assert {row["user_jid"] for row in remaining} == {first.jid, second.jid}
