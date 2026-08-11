"""PRS v2.0 conformance suite.

Drives the real dispatcher with mocked neonize messages against in-memory
SQLite, covering every operation in PRS section 7, the role boundaries in
section 3, and all eight schema field types in section 7.4.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.auth import upsert_user
from db.models import Assignment, Base, Event, Task
from db.report_store import ReportStore
from db.schema_store import FIELD_TYPES, coerce_value, parse_field_spec
from db.subgroup_store import SubgroupStore
from features.registry import register_features


@pytest.fixture
def factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def users(factory):
    return {
        "admin": upsert_user(factory, "911111111111@s.whatsapp.net", role="admin", display_name="Lead"),
        "ananya": upsert_user(factory, "912222222222@s.whatsapp.net", role="member", display_name="Ananya"),
        "bibisha": upsert_user(factory, "913333333333@s.whatsapp.net", role="member", display_name="Bibisha"),
        "shaurya": upsert_user(factory, "914444444444@s.whatsapp.net", role="member", display_name="Shaurya"),
    }


@pytest.fixture
def bot(factory):
    client = MagicMock()
    client.get_group_info.side_effect = Exception("no group info in tests")
    dispatch = register_features(client, {
        "db_session_factory": factory, "group_ids": [], "media_group_id": "",
        "incident_group_id": "", "incident_port": 0, "subgroup_blocked_users": [],
    })

    def send(text, sender, mentions=None):
        client.reset_mock()
        targets = mentions or []
        with patch("features.work._get_mentioned_jids", return_value=targets), \
             patch("features.reports._get_mentioned_jids", return_value=targets), \
             patch("features.labels._get_mentioned_jids", return_value=targets):
            message = MagicMock()
            message.Info.MessageSource.Chat.Server = "g.us"
            message.Info.MessageSource.Sender = sender
            message.Message.conversation = text
            message.Message.extendedTextMessage = None
            message.Message.imageMessage = None
            dispatch(message)
        if not client.send_message.called:
            return "(no reply)"
        reply = client.send_message.call_args[0][1]
        text = getattr(getattr(reply, "extendedTextMessage", None), "text", None)
        return text or reply

    return send


# --- section 7.3 events -----------------------------------------------------

def test_event_create_carries_dates_and_labels(factory, bot, users):
    reply = bot("!work create event | participation | lfx | LFX Term 3 2026 | Apps "
                "| start 2026-01-01 | end 2026-06-01 | labels ml,backend", users["admin"].jid)
    assert "✅ Event `1` created" in reply
    with factory() as session:
        event = session.get(Event, 1)
        assert event.start_date.strftime("%Y-%m-%d") == "2026-01-01"
        assert event.end_date.strftime("%Y-%m-%d") == "2026-06-01"


def test_event_update_operation(factory, bot, users):
    bot("!work create event | participation | gsoc | GSoC 2026 | x", users["admin"].jid)
    reply = bot("!update-event 1 | name GSoC 2026 Revised | end 2026-09-01", users["admin"].jid)
    assert "✅ Event `1` updated" in reply
    with factory() as session:
        assert session.get(Event, 1).name == "GSoC 2026 Revised"


def test_event_update_requires_admin(bot, users):
    bot("!work create event | participation | gsoc | GSoC 2026 | x", users["admin"].jid)
    assert "⛔" in bot("!update-event 1 | name Hacked", users["ananya"].jid)


def test_invalid_event_category_rejected(bot, users):
    reply = bot("!work create event | participation | recruitment | Wrong | x", users["admin"].jid)
    assert "⚠️" in reply


# --- section 7.3 assignment -------------------------------------------------

def test_assign_multiple_mentions_in_one_command(factory, bot, users):
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    reply = bot("!work assign event 1 | @Ananya @Bibisha", users["admin"].jid,
                [users["ananya"].jid, users["bibisha"].jid])
    assert "✅ Assigned" in reply
    with factory() as session:
        assert len(session.query(Assignment).filter_by(event_id=1).all()) == 2


def test_assign_by_label_expands_to_members(factory, bot, users):
    SubgroupStore(factory).write({"third-years": [users["ananya"].jid, users["bibisha"].jid,
                                                  users["shaurya"].jid]})
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    reply = bot("!work assign event 1 | @third-years", users["admin"].jid, [])
    assert "✅ Assigned" in reply
    with factory() as session:
        assert len(session.query(Assignment).filter_by(event_id=1).all()) == 3


def test_unassign_by_label(factory, bot, users):
    SubgroupStore(factory).write({"third-years": [users["ananya"].jid, users["bibisha"].jid]})
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    bot("!work assign event 1 | @third-years", users["admin"].jid, [])
    reply = bot("!work unassign event 1 | @third-years", users["admin"].jid, [])
    assert "Removed 2 assignment(s)" in reply
    with factory() as session:
        assert session.query(Assignment).filter_by(event_id=1).all() == []


def test_assign_requires_admin(bot, users):
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    assert "⛔" in bot("!work assign event 1 | @Ananya", users["ananya"].jid, [users["ananya"].jid])


# --- section 7.5 tasks ------------------------------------------------------

def test_task_links_to_organization_event(factory, bot, users):
    bot("!work create event | organization | recruitment | Recruitment 2026 | x", users["admin"].jid)
    reply = bot("!work create task | Screening round | Shortlist | event 1 | priority high",
                users["admin"].jid)
    assert "✅ Task `1` created" in reply
    with factory() as session:
        task = session.get(Task, 1)
        assert task.event_id == 1
        assert task.priority == "high"
    assert "Screening round" in bot("!work tasks event 1", users["admin"].jid)
    overview = bot("!work", users["admin"].jid)
    assert "Recruitment 2026" in overview
    assert "Screening round" in overview
    assert "└─" in overview


def test_task_event_link_rejects_non_numeric(bot, users):
    assert "⚠️" in bot("!work create task | T | d | event abc", users["admin"].jid)


def test_task_complete_syncs_lifecycle(factory, bot, users):
    bot("!work create task | Prepare report | d | due 2026-08-01", users["admin"].jid)
    bot("!work assign task 1 | @Ananya", users["admin"].jid, [users["ananya"].jid])
    assert "completed" in bot("!work complete task 1", users["ananya"].jid)
    with factory() as session:
        assert session.get(Task, 1).status == "done"


# --- section 7.4 schemas ----------------------------------------------------

@pytest.fixture
def schema_event(bot, users):
    bot("!work create event | participation | lfx | LFX Term 3 2026 | Apps", users["admin"].jid)
    bot("!schema set event 1 | org single_select(linkerd,istio,kubernetes) | prs number "
        "| accepted boolean | proposal url | deadline date | tags multi_select(gsoc,lfx) "
        "| links list | mentor text", users["admin"].jid)
    bot("!work assign event 1 | @Ananya", users["admin"].jid, [users["ananya"].jid])
    return 1


def test_schema_defines_all_eight_field_types(bot, users, schema_event):
    reply = bot("!schema event 1", users["ananya"].jid)
    for field_type in FIELD_TYPES:
        assert field_type in reply


@pytest.mark.parametrize("field,value", [
    ("org", "linkerd"), ("org", "LINKERD"), ("prs", "3"), ("accepted", "yes"),
    ("proposal", "https://example.com/p"), ("deadline", "2026-08-01"),
    ("tags", "gsoc,lfx"), ("links", "a,b,c"), ("mentor", "Jane Doe"),
])
def test_valid_values_accepted(bot, users, schema_event, field, value):
    assert "✅ Update" in bot(f"!work update event 1 {field} {value}", users["ananya"].jid)


@pytest.mark.parametrize("field,value", [
    ("org", "nginx"), ("prs", "banana"), ("accepted", "maybe"),
    ("proposal", "notaurl"), ("deadline", "soon"), ("tags", "gsoc,bogus"),
])
def test_invalid_values_rejected(bot, users, schema_event, field, value):
    assert "⚠️" in bot(f"!work update event 1 {field} {value}", users["ananya"].jid)


def test_unknown_field_rejected_with_valid_list(bot, users, schema_event):
    reply = bot("!work update event 1 nonsense x", users["ananya"].jid)
    assert "is not a field on this event" in reply
    assert "org" in reply


def test_schema_canonicalises_select_case(factory, bot, users, schema_event):
    bot("!work update event 1 org LINKERD", users["ananya"].jid)
    cohort = ReportStore(factory).cohort(1)
    assert cohort["rows"][0]["values"]["org"] == "linkerd"


def test_edit_also_validates_against_schema(bot, users, schema_event):
    reply = bot("!work update event 1 prs 3", users["ananya"].jid)
    revision_id = int(reply.split("`")[1])
    assert "⚠️" in bot(f"!work edit {revision_id} banana", users["ananya"].jid)
    assert "edited successfully" in bot(f"!work edit {revision_id} 9", users["ananya"].jid)


def test_events_without_schema_stay_free_form(bot, users):
    bot("!work create event | participation | research | Paper | x", users["admin"].jid)
    bot("!work assign event 1 | @Ananya", users["admin"].jid, [users["ananya"].jid])
    assert "✅ Update" in bot("!work update event 1 anything at all", users["ananya"].jid)


def test_schema_mutation_requires_admin(bot, users, schema_event):
    assert "⛔" in bot("!schema set event 1 | x text", users["ananya"].jid)


def test_schema_remove_and_clear(bot, users, schema_event):
    assert "removed" in bot("!schema remove event 1 | mentor", users["admin"].jid)
    assert "Cleared" in bot("!schema clear event 1", users["admin"].jid)


def test_field_names_are_case_insensitive(factory, bot, users):
    bot("!work create event | participation | research | Paper | x", users["admin"].jid)
    bot("!work assign event 1 | @Ananya", users["admin"].jid, [users["ananya"].jid])
    bot("!work update event 1 prs 3", users["ananya"].jid)
    bot("!work update event 1 PRs 7", users["ananya"].jid)
    values = ReportStore(factory).cohort(1)["rows"][0]["values"]
    assert values == {"prs": "7"}


def test_prs_schema_verb_spellings(bot, users):
    """PRS names these schema.create/update/delete rather than set/add/remove."""
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    assert "`org`" in bot("!schema create event 1 | org text | prs number", users["admin"].jid)
    assert "`accepted`" in bot("!schema update event 1 | accepted boolean", users["admin"].jid)
    assert "boolean" in bot("!schema fields event 1", users["admin"].jid)
    assert "removed" in bot("!schema delete event 1 | accepted", users["admin"].jid)
    assert "Cleared 2" in bot("!schema delete event 1", users["admin"].jid)


@pytest.mark.parametrize("spec", ["org single_select", "org bogus_type", ""])
def test_bad_field_specs_rejected(spec):
    with pytest.raises(ValueError):
        parse_field_spec(spec)


def test_number_coercion_normalises():
    assert coerce_value("number", "3.0", None) == "3"
    assert coerce_value("number", "3.5", None) == "3.5"


# --- section 7.8 reports ----------------------------------------------------

def test_cohort_report_pivots_latest_values(bot, users, schema_event):
    bot("!work update event 1 org linkerd", users["ananya"].jid)
    bot("!work update event 1 prs 2", users["ananya"].jid)
    bot("!work update event 1 prs 7", users["ananya"].jid)
    reply = bot("!reports progress event 1", users["admin"].jid)
    assert "Ananya" in reply and "linkerd" in reply
    assert "7" in reply and " 2 " not in reply


def test_cohort_report_omits_duplicate_status_column(bot, users, schema_event):
    bot("!work start event 1", users["ananya"].jid)
    reply = bot("!reports progress event 1", users["admin"].jid)
    assert reply.count("status") == 1


def test_cohort_table_stays_phone_readable(bot, users):
    """Lists of PR links must collapse to a count instead of blowing out the width."""
    bot("!work create event | participation | lfx | LFX Term 3 2026 | Apps", users["admin"].jid)
    bot("!schema create event 1 | orgs list | prs_opened list", users["admin"].jid)
    bot("!work assign event 1 | @Ananya", users["admin"].jid, [users["ananya"].jid])
    bot("!work update event 1 orgs fluentd,keploy,istio", users["ananya"].jid)
    bot("!work update event 1 prs_opened https://github.com/fluent/fluentd/pull/4412,"
        "https://github.com/keploy/keploy/pull/2201,https://github.com/istio/istio/pull/51203",
        users["ananya"].jid)
    reply = bot("!reports progress event 1", users["admin"].jid)
    assert "3 items" in reply
    assert max(len(line) for line in reply.splitlines()) < 110
    # The full links stay retrievable for the individual.
    history = bot(f"!work history event 1 @{users['ananya'].jid}", users["admin"].jid)
    assert "pull/2201" in history


def test_report_summary_and_status_lists(bot, users, schema_event):
    assert "📈 *Work Report*" in bot("!reports", users["admin"].jid)
    assert "Ananya" in bot("!reports pending", users["admin"].jid)
    bot("!work complete event 1", users["ananya"].jid)
    assert "Ananya" in bot("!reports completed", users["admin"].jid)


def test_reports_require_admin(bot, users, schema_event):
    assert "⛔" in bot("!reports", users["ananya"].jid)


# --- section 7.9 audit ------------------------------------------------------

def test_audit_records_work_operations(bot, users, schema_event):
    bot("!work update event 1 prs 4", users["ananya"].jid)
    reply = bot("!audit", users["admin"].jid)
    for operation in ("event.create", "schema.set", "event.assign", "update.submit"):
        assert operation in reply


def test_audit_filters_by_operation(bot, users, schema_event):
    bot("!work update event 1 prs 4", users["ananya"].jid)
    reply = bot("!audit update", users["admin"].jid)
    assert "update.submit" in reply and "event.create" not in reply


def test_audit_requires_admin(bot, users):
    assert "⛔" in bot("!audit", users["ananya"].jid)


# --- section 7.2 labels -----------------------------------------------------

def test_label_lifecycle(factory, bot, users):
    assert "now has 2 member(s)" in bot("!labels create third-years | @Ananya @Bibisha",
                                        users["admin"].jid, [users["ananya"].jid, users["bibisha"].jid])
    assert "`third-years`" in bot("!labels", users["admin"].jid)
    assert "third-years" in bot("!labels of @Ananya", users["ananya"].jid, [users["ananya"].jid])
    assert "Removed 1" in bot("!labels remove third-years | @Ananya", users["admin"].jid,
                              [users["ananya"].jid])
    assert "deleted" in bot("!labels delete third-years", users["admin"].jid)


def test_user_can_hold_multiple_labels(factory, bot, users):
    bot("!labels create backend | @Ananya", users["admin"].jid, [users["ananya"].jid])
    bot("!labels create third-years | @Ananya", users["admin"].jid, [users["ananya"].jid])
    reply = bot("!labels of @Ananya", users["admin"].jid, [users["ananya"].jid])
    assert "backend" in reply and "third-years" in reply


def test_member_can_self_serve_labels(factory, bot, users):
    """Anyone may opt themselves into or out of a label."""
    reply = bot("!labels add lfx-applicants | @Ananya", users["ananya"].jid, [users["ananya"].jid])
    assert "now has 1 member(s)" in reply
    # A bare add with no mention means "add me".
    assert "now has 1 member(s)" in bot("!labels add gsoc-hopefuls", users["bibisha"].jid)
    assert "gsoc-hopefuls" in bot("!labels of @Bibisha", users["bibisha"].jid, [users["bibisha"].jid])
    assert "Removed 1" in bot("!labels remove lfx-applicants", users["ananya"].jid)


def test_member_cannot_move_someone_else(bot, users):
    reply = bot("!labels add lfx-applicants | @Bibisha", users["ananya"].jid, [users["bibisha"].jid])
    assert "only add or remove yourself" in reply
    assert "`lfx-applicants`" not in bot("!labels", users["ananya"].jid)


def test_member_cannot_delete_a_label(bot, users):
    bot("!labels create lfx-applicants | @Ananya @Bibisha", users["admin"].jid,
        [users["ananya"].jid, users["bibisha"].jid])
    assert "⛔" in bot("!labels delete lfx-applicants", users["ananya"].jid)
    assert "`lfx-applicants`" in bot("!labels", users["admin"].jid)


def test_admin_can_still_move_anyone(bot, users):
    reply = bot("!labels create lfx-applicants | @Ananya @Bibisha", users["admin"].jid,
                [users["ananya"].jid, users["bibisha"].jid])
    assert "now has 2 member(s)" in reply


def test_invalid_label_name_rejected(bot, users):
    assert "⚠️" in bot("!labels create a | @Ananya", users["admin"].jid, [users["ananya"].jid])


# --- section 6 reminders ----------------------------------------------------

def test_reminder_run_after_an_update_does_not_crash(bot, users, schema_event):
    """Regression: comparing naive and aware datetimes broke reminder.run."""
    bot("!work update event 1 prs 3", users["ananya"].jid)
    reply = bot("!work reminders run", users["admin"].jid)
    assert "Reminder Run Completed" in reply
    assert "⚠️" not in reply


def test_reminder_config_supports_weekly_cadence(bot, users):
    reply = bot("!work reminders config frequency 168 | threshold 2", users["admin"].jid)
    assert "168h" in reply


# --- section 3 role boundaries ---------------------------------------------

def test_member_sees_only_own_workload(bot, users):
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    bot("!work assign event 1 | @Bibisha", users["admin"].jid, [users["bibisha"].jid])
    assert "No matching work" in bot("!my", users["ananya"].jid)


def test_member_cannot_update_another_members_assignment(bot, users):
    bot("!work create event | participation | lfx | LFX | x", users["admin"].jid)
    bot("!work assign event 1 | @Bibisha", users["admin"].jid, [users["bibisha"].jid])
    assert "⚠️" in bot("!work update event 1 note hijack", users["ananya"].jid)
    assert "⚠️" in bot(
        f"!work update event 1 @{users['bibisha'].jid} note hijack",
        users["ananya"].jid,
    )
