from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from features.agent_runtime import MAX_PLAN_STEPS, tool_spec, validate_plan_preflight, validate_registry


def test_plan_preflight_accepts_dependencies_on_prior_steps():
    assert validate_plan_preflight([
        {"step_id": "event", "capability": "work.create_event", "arguments": {"name": "Event"}},
        {
            "step_id": "task",
            "capability": "work.create_task",
            "arguments": {"title": "Task", "event_id": "$event.event_id"},
        },
    ]) is None


def test_plan_budget_supports_real_compound_workflows():
    assert MAX_PLAN_STEPS >= 12


def test_tool_registry_has_no_schema_or_policy_drift():
    assert validate_registry() == []


def test_every_installed_neonize_callable_is_classified():
    from features.neonize_policy import audit_neonize_surface, policy_reason

    audit = audit_neonize_surface()
    assert audit["unclassified"] == []
    assert audit["stale_exposed"] == []
    assert audit["stale_excluded"] == []
    assert audit["conflicts"] == []
    assert policy_reason("get_group_info") == "exposed through a typed, authorized agent tool"
    assert "session" in policy_reason("connect")


def test_plan_preflight_accepts_structured_audience_output():
    assert validate_plan_preflight([
        {"step_id": "group", "capability": "whatsapp.group_members", "arguments": {}},
        {
            "step_id": "add",
            "capability": "collections.add",
            "arguments": {
                "collection": "everyone",
                "audience": {"resolver": "plan_output", "value": "$group.member_jids"},
            },
        },
    ]) is None


def test_plan_preflight_accepts_plan_produced_chat_context():
    assert validate_plan_preflight([
        {"step_id": "created", "capability": "whatsapp.create_group", "arguments": {"name": "Team"}},
        {
            "step_id": "announce",
            "capability": "whatsapp.send",
            "arguments": {
                "text": "Welcome",
                "target_chat": {"resolver": "plan_output", "value": "$created.group_jid"},
            },
        },
    ]) is None


def test_plan_preflight_accepts_two_plan_produced_group_endpoints():
    assert validate_plan_preflight([
        {"step_id": "parent", "capability": "whatsapp.group_info_from_link", "arguments": {"link": "abc"}},
        {"step_id": "child", "capability": "whatsapp.create_group", "arguments": {"name": "Child"}},
        {
            "step_id": "link",
            "capability": "whatsapp.link_group",
            "arguments": {
                "parent_chat": {"resolver": "plan_output", "value": "$parent.group_jid"},
                "child_chat": {"resolver": "plan_output", "value": "$child.group_jid"},
            },
        },
    ]) is None


def test_plan_preflight_rejects_later_dependency():
    error = validate_plan_preflight([
        {
            "step_id": "task",
            "capability": "work.create_task",
            "arguments": {"title": "Task", "event_id": "$event.event_id"},
        },
        {"step_id": "event", "capability": "work.create_event", "arguments": {"name": "Event"}},
    ])
    assert "later or unknown" in error


def test_plan_preflight_rejects_output_not_declared_by_producer():
    error = validate_plan_preflight([
        {"step_id": "event", "capability": "work.create_event", "arguments": {"name": "Event"}},
        {
            "step_id": "task",
            "capability": "work.create_task",
            "arguments": {"title": "Task", "event_id": "$event.task_id"},
        },
    ])
    assert "unavailable output" in error


def test_plan_preflight_rejects_duplicate_step_ids():
    error = validate_plan_preflight([
        {"step_id": "same", "capability": "work.create_event", "arguments": {"name": "Event"}},
        {"step_id": "same", "capability": "work.create_task", "arguments": {"title": "Task"}},
    ])
    assert "duplicate" in error


def test_plan_preflight_rejects_missing_required_tool_argument():
    error = validate_plan_preflight([
        {"step_id": "task", "capability": "work.create_task", "arguments": {}},
    ])
    assert "requires argument title" in error


def test_neonize_adapters_are_registered_as_narrow_tools():
    assert tool_spec("whatsapp.send").mutating is True
    assert tool_spec("whatsapp.send").required == ("text",)
    assert tool_spec("whatsapp.reply").required == ("text",)
    assert tool_spec("whatsapp.react").required == ("reaction",)
    assert tool_spec("whatsapp.group_info").mutating is False
    assert tool_spec("whatsapp.user_info").required == ("audience",)
    assert tool_spec("work.create_event").permission == "admin"
    assert tool_spec("work.delete_event").destructive is True
    assert tool_spec("whatsapp.group_members").permission == "member"
    assert tool_spec("whatsapp.send_attachment").executor == "direct"
    assert tool_spec("whatsapp.joined_groups").produces == frozenset({"groups", "group_count"})
    assert tool_spec("whatsapp.community_subgroups").permission == "member"
    assert tool_spec("whatsapp.set_group_topic").permission == "admin"
    assert tool_spec("whatsapp.send_poll").required == ("question", "options")


def test_direct_execution_set_is_derived_from_tool_registry():
    from features.nl_operations import DIRECT_CAPABILITIES, validate_direct_registry

    assert "whatsapp.react" in DIRECT_CAPABILITIES
    assert "whatsapp.group_members" in DIRECT_CAPABILITIES
    assert "work.delete_task" not in DIRECT_CAPABILITIES
    assert validate_direct_registry() == {"missing": [], "unknown": [], "overlap": []}


def test_group_metadata_adapter_returns_structured_member_context():
    from features.nl_operations import _serialize_group_info

    info = SimpleNamespace(
        JID="120@g.us",
        GroupName="PBBot",
        GroupTopic="Work",
        Participants=[
            SimpleNamespace(
                JID="123@s.whatsapp.net", LID="", PhoneNumber="123",
                DisplayName="Ananya", IsAdmin=True, IsSuperAdmin=False,
            ),
        ],
    )
    result = _serialize_group_info(info)
    assert result["member_count"] == 1
    assert result["member_jids"] == ["123@s.whatsapp.net"]
    assert result["members"][0]["display_name"] == "Ananya"


def test_group_membership_adapter_uses_current_chat_and_admin_gate():
    from features.nl_operations import execute_whatsapp_group_membership

    client = MagicMock()
    message = SimpleNamespace(
        Info=SimpleNamespace(
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender="admin@s.whatsapp.net",
            )
        )
    )
    intent = {"capability": "whatsapp.add_group_members", "arguments": {}}
    with patch("db.auth.gate", return_value=SimpleNamespace(role="admin")):
        result = execute_whatsapp_group_membership(
            client, message, intent, ["123@s.whatsapp.net"], MagicMock()
        )
    assert result["action"] == "add"
    client.update_group_participants.assert_called_once()
    assert client.update_group_participants.call_args.args[0] is message.Info.MessageSource.Chat


def test_new_neonize_discovery_tools_are_registered_with_outputs():
    assert tool_spec("whatsapp.profile_pictures").required == ("audience",)
    assert tool_spec("whatsapp.profile_pictures").produces == frozenset({"profiles", "profile_count"})
    assert tool_spec("whatsapp.group_join_requests").produces == frozenset({"requests", "request_count"})
    assert tool_spec("whatsapp.linked_group_members").produces == frozenset({"members", "member_count"})


def test_profile_picture_adapter_returns_structured_rows_for_resolved_audience():
    from features.nl_operations import execute_whatsapp_profile_pictures

    client = MagicMock()
    client.get_profile_picture.return_value = SimpleNamespace(URL="https://example.test/p.png", ID="pic-1")
    message = SimpleNamespace(
        Info=SimpleNamespace(
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender="admin@s.whatsapp.net",
            )
        )
    )
    with patch("db.auth.gate", return_value=SimpleNamespace(role="admin")):
        result = execute_whatsapp_profile_pictures(
            client, message, {"capability": "whatsapp.profile_pictures"},
            ["123@s.whatsapp.net"], MagicMock()
        )
    assert result["profile_count"] == 1
    assert result["profiles"][0]["url"] == "https://example.test/p.png"
    client.get_profile_picture.assert_called_once()


def test_group_discovery_adapters_return_machine_consumable_rows():
    from features.nl_operations import (
        execute_whatsapp_group_join_requests,
        execute_whatsapp_linked_group_members,
    )

    client = MagicMock()
    client.get_group_request_participants.return_value = [
        SimpleNamespace(JID="123@s.whatsapp.net", TimeAt=42)
    ]
    client.get_linked_group_participants.return_value = [
        SimpleNamespace(JID="456@s.whatsapp.net")
    ]
    message = SimpleNamespace(
        Info=SimpleNamespace(
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender="admin@s.whatsapp.net",
            )
        )
    )
    with patch("db.auth.gate", return_value=SimpleNamespace(role="admin")):
        requests = execute_whatsapp_group_join_requests(
            client, message, {"capability": "whatsapp.group_join_requests"}, MagicMock()
        )
        linked = execute_whatsapp_linked_group_members(
            client, message, {"capability": "whatsapp.linked_group_members"}, MagicMock()
        )
    assert requests["requests"] == [{"jid": "123@s.whatsapp.net", "requested_at": "42"}]
    assert linked["members"] == [{"jid": "456@s.whatsapp.net", "requested_at": ""}]


def test_lifecycle_tools_have_required_arguments_and_policies():
    assert validate_plan_preflight([
        {"step_id": "create", "capability": "whatsapp.create_group", "arguments": {"name": "Team"}},
        {"step_id": "join", "capability": "whatsapp.join_group", "arguments": {"invite": "abc"}},
        {"step_id": "check", "capability": "whatsapp.is_on_whatsapp", "arguments": {"numbers": ["+123456789"]}},
    ]) is None
    assert tool_spec("whatsapp.leave_group").destructive is True
    assert tool_spec("whatsapp.revoke_message").destructive is True
    assert tool_spec("whatsapp.block_contacts").destructive is True
    assert tool_spec("whatsapp.set_group_photo").permission == "admin"
    assert tool_spec("whatsapp.contact_devices").required == ("audience",)
    assert tool_spec("whatsapp.blocklist").produces == frozenset({"contacts", "contact_count"})
    assert tool_spec("whatsapp.link_group").required == ("parent_chat", "child_chat")
    assert tool_spec("whatsapp.unlink_group").destructive is True
    assert tool_spec("whatsapp.contact_qr").produces == frozenset({"link", "revoked"})
    assert tool_spec("whatsapp.set_profile_name").permission == "admin"
    assert tool_spec("whatsapp.account_info").produces == frozenset({"jid", "lid", "name", "platform"})


def test_message_and_contact_adapters_use_runtime_context_not_model_ids():
    from features.nl_operations import (
        execute_whatsapp_blocklist,
        execute_whatsapp_message_moderation,
    )

    client = MagicMock()
    message = SimpleNamespace(
        Info=SimpleNamespace(
            ID="message-1",
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender="admin@s.whatsapp.net",
            ),
        )
    )
    with patch("db.auth.gate", return_value=SimpleNamespace(role="admin")):
        result = execute_whatsapp_blocklist(
            client, message,
            {"capability": "whatsapp.block_contacts", "arguments": {}},
            ["123@s.whatsapp.net"], MagicMock()
        )
        pinned = execute_whatsapp_message_moderation(
            client, message,
            {"capability": "whatsapp.pin_message", "arguments": {"seconds": 60}},
            MagicMock()
        )
    assert result["count"] == 1
    assert pinned["message_id"] == "message-1"
    client.update_blocklist.assert_called_once()
    client.pin_message.assert_called_once_with(
        message.Info.MessageSource.Chat,
        message.Info.MessageSource.Sender,
        "message-1",
        60,
    )


def test_direct_dispatch_routes_a_plan_produced_chat_to_the_adapter():
    from features.nl_operations import execute_direct_tool

    client = MagicMock()
    message = SimpleNamespace(
        Info=SimpleNamespace(
            ID="message-1",
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender="admin@s.whatsapp.net",
            ),
        )
    )
    with patch("db.auth.gate", return_value=SimpleNamespace(role="admin")):
        result = execute_direct_tool(
            client,
            message,
            {
                "capability": "whatsapp.send",
                "arguments": {"text": "Welcome", "target_chat": "999@g.us"},
            },
            [],
            MagicMock(),
        )
    assert result["sent"] is True
    routed_chat = client.send_message.call_args.args[0]
    assert routed_chat.User == "999"
    assert routed_chat.Server == "g.us"


def test_neonize_jid_outputs_are_canonical_for_followup_plan_steps():
    from features.nl_operations import _jid_text
    from neonize.utils import build_jid

    assert _jid_text(build_jid("999", "g.us")) == "999@g.us"
    assert _jid_text("999@g.us") == "999@g.us"
