from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message

from features.nl_runtime import (
    TargetResolution,
    resolve_target,
    target_is_required_and_missing,
    validate_execution_ready,
)
from features.labels import add_label_members, remove_label_members
from features.subgroups import add_subgroup_members


def make_group_message(sender="member@s.whatsapp.net"):
    message = MagicMock()
    message.Info.MessageSource.Chat.Server = "g.us"
    message.Info.MessageSource.Sender = sender
    return message


def make_real_group_message(sender="member@s.whatsapp.net"):
    return SimpleNamespace(
        Message=Message(conversation=""),
        Info=SimpleNamespace(
            MessageSource=SimpleNamespace(
                Chat=SimpleNamespace(Server="g.us"),
                Sender=sender,
            )
        ),
    )


def test_required_capabilities_are_incomplete_without_an_audience():
    assert target_is_required_and_missing(
        {"capability": "collections.add", "arguments": {"collection": "everyone"}},
        [],
    )
    assert target_is_required_and_missing(
        {
            "capability": "work.assign",
            "arguments": {"target": "task 7", "target_id": 7},
        },
        [],
    )


def test_work_item_target_is_not_confused_with_an_audience():
    intent = {
        "capability": "work.assign",
        "arguments": {
            "target": "task 7",
            "target_type": "task",
            "target_id": 7,
        },
    }
    assert target_is_required_and_missing(intent, []) is True


def test_explicit_mentions_are_resolved_and_bot_is_removed():
    message = make_group_message()
    message._pbbot_visible_mentions = [
        "111@s.whatsapp.net",
        "bot@s.whatsapp.net",
        "111@s.whatsapp.net",
    ]
    result = resolve_target(
        MagicMock(),
        message,
        {
            "capability": "collections.add",
            "arguments": {
                "collection": "backend",
                "audience": {"resolver": "explicit_mentions"},
            },
        },
        {"bot@s.whatsapp.net"},
        None,
        lambda value: value,
        ["111@s.whatsapp.net", "bot@s.whatsapp.net", "111@s.whatsapp.net"],
    )
    assert result.ready
    assert result.members == ("111@s.whatsapp.net",)


def test_current_group_lookup_exception_is_controlled():
    message = make_group_message()
    intent = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }
    with patch(
        "features.community_tag.get_group_member_jids",
        side_effect=RuntimeError("metadata unavailable"),
    ):
        result = resolve_target(
            MagicMock(), message, intent, set(), None, lambda value: value
        )
    assert result.ready is False
    assert "resolve" in result.error


def test_real_neonize_message_is_not_mutated_with_runtime_attributes():
    message = make_real_group_message()
    intent = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }
    with patch(
        "features.community_tag.get_group_member_jids",
        return_value=["111@s.whatsapp.net"],
    ):
        result = resolve_target(
            MagicMock(), message, intent, set(), None, lambda value: value
        )
    assert result.members == ("111@s.whatsapp.net",)
    assert not hasattr(message.Message, "_pbbot_visible_mentions")


def test_empty_or_bot_only_group_cannot_execute():
    message = make_group_message()
    intent = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }
    for members in ([], ["bot@s.whatsapp.net"]):
        with patch("features.community_tag.get_group_member_jids", return_value=members):
            result = resolve_target(
                MagicMock(), message, intent, {"bot@s.whatsapp.net"}, None, lambda value: value
            )
        assert result.ready is False
        assert "eligible" in result.error


def test_non_group_chat_cannot_resolve_current_members():
    message = make_group_message()
    message.Info.MessageSource.Chat.Server = "s.whatsapp.net"
    intent = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }
    result = resolve_target(MagicMock(), message, intent, set(), None, lambda value: value)
    assert "unavailable" in result.error


def test_collection_and_admin_resolvers_fail_closed_without_required_context():
    message = make_group_message()
    collection_intent = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "collection_members", "value": "missing"},
        },
    }
    result = resolve_target(
        MagicMock(), message, collection_intent, set(), MagicMock(), lambda value: None
    )
    assert "not found" in result.error

    admin_intent = {
        "capability": "work.assign",
        "arguments": {
            "target_type": "task",
            "target_id": 7,
            "audience": {"resolver": "active_admins"},
        },
    }
    result = resolve_target(
        MagicMock(), message, admin_intent, set(), None, lambda value: value
    )
    assert "unavailable" in result.error


def test_unknown_resolver_and_missing_required_target_are_rejected():
    message = make_group_message()
    unknown = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "invented_users"},
        },
    }
    result = resolve_target(MagicMock(), message, unknown, set(), None, lambda value: value)
    assert "not available" in result.error

    error = validate_execution_ready(
        {"capability": "collections.add", "arguments": {"collection": "everyone"}},
        TargetResolution(),
        [],
    )
    assert "identify who" in error


def test_failed_resolution_cannot_be_treated_as_ready():
    intent = {
        "capability": "work.assign",
        "arguments": {
            "target_type": "event",
            "target_id": 7,
            "audience": {"resolver": "current_chat_members"},
        },
    }
    error = validate_execution_ready(
        intent,
        TargetResolution(error="metadata unavailable"),
        [],
    )
    assert error == "metadata unavailable"


def test_subgroup_domain_operation_persists_resolved_members_directly():
    store = MagicMock()
    store.read.return_value = {}

    added, total = add_subgroup_members(
        store,
        "everyone",
        ["111@s.whatsapp.net", "222@s.whatsapp.net", "111@s.whatsapp.net"],
    )

    assert (added, total) == (2, 2)
    store.write.assert_called_once_with({
        "everyone": ["111@s.whatsapp.net", "222@s.whatsapp.net"],
    })


def test_label_domain_operations_accept_resolved_members_directly():
    store = MagicMock()
    store.read.return_value = {}
    added, total = add_label_members(
        store, "everybody", ["111@s.whatsapp.net", "222@s.whatsapp.net"]
    )
    assert added == ["111@s.whatsapp.net", "222@s.whatsapp.net"]
    assert total == 2

    store.read.return_value = {
        "everybody": ["111@s.whatsapp.net", "222@s.whatsapp.net"]
    }
    removed, deleted = remove_label_members(
        store, "everybody", ["111@s.whatsapp.net"]
    )
    assert (removed, deleted) == (1, False)
