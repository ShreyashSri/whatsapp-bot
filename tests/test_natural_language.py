import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from features.natural_language import (
    MISTRAL_CHAT_URL,
    MistralCardDesigner,
    MistralCommandTranslator,
    _admin_target_suffix,
    build_knowledge_context,
    compile_card_design,
    compile_intent,
    fallback_command,
    register,
    _plan_target_collision_error,
    _resolve_target_reference,
    _resolve_collection_name,
    _resolve_collection_names,
    _resolve_runtime_target_scope,
    resolve_named_collection_command,
    resolve_named_entity_command,
    validate_command,
    validate_intent,
    validate_plan,
    _needs_target_repair,
    _inherit_plan_context,
    _target_arguments,
    _typed_target_parts,
    _intent_compile_error,
    _intent_completeness_issue,
    _plan_completeness_issue,
    _repair_collection_tag_intent,
    _default_generic_event_fields,
    _natural_work_target,
    is_bot_mentioned,
)
from features.subgroups import normalize_collection_name
from features.nl_runtime import target_expression


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def test_me_alias_is_recognized_without_live_identity_lookup():
    message = make_message("@me help work")
    client = MagicMock()
    client.get_me.side_effect = AssertionError("get_me must not be needed for @me")

    assert is_bot_mentioned(message, client, {}) is True
    client.get_me.assert_not_called()


class SequenceHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


def make_message(text, sender="member@s.whatsapp.net", server="g.us"):
    message = MagicMock()
    message.Info.MessageSource.Chat.Server = server
    message.Info.MessageSource.Sender = sender
    message.Message.conversation = text
    message.Message.extendedTextMessage = None
    message.Message.imageMessage = None
    return message


def test_mistral_returns_a_single_existing_command_in_json_mode():
    http = FakeHttpClient(
        FakeResponse({"choices": [{"message": {"content": '{"command":"!my","clarification":""}'}}]})
    )
    translator = MistralCommandTranslator("secret", client=http)

    command, clarification = translator.translate(
        "can you show me what I need to do?", ["123@s.whatsapp.net"]
    )

    assert command == "!my"
    assert clarification == ""
    assert http.calls[0][0][0] == MISTRAL_CHAT_URL
    assert http.calls[0][1]["json"]["response_format"] == {"type": "json_object"}
    assert http.calls[0][1]["json"]["temperature"] == 0


def test_mistral_returns_a_structured_capability_envelope():
    http = FakeHttpClient(
        FakeResponse({
            "choices": [{"message": {"content":
                '{"intent":{"capability":"labels.add","arguments":{"collection":"team","mention_indices":[0]}},"clarification":""}'
            }}]
        })
    )
    translator = MistralCommandTranslator("secret", client=http)

    intent, clarification = translator.translate("put the person in team", ["person@lid"])

    assert intent == {
        "capability": "labels.add",
        "arguments": {"collection": "team", "mention_indices": [0]},
    }
    assert clarification == ""
    assert "capability" in http.calls[0][1]["json"]["messages"][0]["content"]


def test_gemini_provider_uses_native_endpoint_and_strips_thinking_trace():
    # Gemma's thinking mode emits a reasoning-trace part marked "thought":
    # true alongside the real answer part; only the non-thought parts should
    # reach JSON parsing. Newer ("AQ."-prefixed) Gemini keys are also only
    # accepted on the native generateContent endpoint with x-goog-api-key,
    # never Bearer auth on an OpenAI-compatible path.
    http = FakeHttpClient(
        FakeResponse({
            "candidates": [{
                "content": {"parts": [
                    {"text": "thinking about it...", "thought": True},
                    {"text": '{"intent":{"capability":"labels.add",'
                             '"arguments":{"collection":"team","mention_indices":[0]}},'
                             '"clarification":""}'},
                ]}
            }]
        })
    )
    translator = MistralCommandTranslator(
        "AQ.secret", "gemma-4-31b-it", client=http, provider="gemini",
    )

    intent, clarification = translator.translate("put the person in team", ["person@lid"])

    assert intent == {
        "capability": "labels.add",
        "arguments": {"collection": "team", "mention_indices": [0]},
    }
    assert clarification == ""

    call_args, call_kwargs = http.calls[0]
    assert call_args[0] == "https://generativelanguage.googleapis.com/v1beta/models/gemma-4-31b-it:generateContent"
    assert call_kwargs["headers"]["x-goog-api-key"] == "AQ.secret"
    assert "Authorization" not in call_kwargs["headers"]
    assert call_kwargs["json"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"
    assert call_kwargs["json"]["generationConfig"]["responseMimeType"] == "application/json"


def test_bare_json_array_plan_is_normalized_to_the_plan_envelope():
    # Observed live against Gemma 4: despite the system prompt documenting
    # {"plan": [...], "clarification": ""}, the model sometimes returns a
    # bare JSON array of steps instead. That's still unambiguous -- there's
    # nothing else a bare array could mean here -- so it must not be
    # rejected as invalid JSON just because it wasn't wrapped.
    http = FakeHttpClient(
        FakeResponse({
            "choices": [{"message": {"content": json.dumps([
                {"step_id": "task1", "capability": "work.assign",
                 "arguments": {"target_type": "task", "target_name": "where",
                               "audience": {"resolver": "explicit_mentions", "mention_indices": [0]}}},
                {"step_id": "task2", "capability": "work.assign",
                 "arguments": {"target_type": "task", "target_name": "how",
                               "audience": {"resolver": "explicit_mentions", "mention_indices": [1]}}},
            ])}}]
        })
    )
    translator = MistralCommandTranslator("secret", client=http)

    with patch("features.agent_runtime.validate_plan_preflight", return_value=None):
        result, clarification = translator.translate(
            "assign task where to @Bibisha and task how to @Ananya",
            ["bibisha@s.whatsapp.net", "ananya@s.whatsapp.net"],
        )

    assert clarification == ""
    assert result is not None
    assert len(result["plan"]) == 2
    assert result["plan"][0]["arguments"]["target_name"] == "where"
    assert result["plan"][1]["arguments"]["target_name"] == "how"


def test_mistral_retries_transient_provider_responses():
    first = FakeResponse({
        "choices": [{"message": {"content": '{"command":"!my"}'}}]
    })
    first.status_code = 503
    second = FakeResponse({
        "choices": [{"message": {"content": '{"command":"!my"}'}}]
    })
    second.status_code = 200
    http = SequenceHttpClient([first, second])
    translator = MistralCommandTranslator("secret", client=http)

    with patch("features.natural_language.time.sleep"):
        command, error = translator.translate("show my work", [])

    assert command == "!my"
    assert error == ""
    assert len(http.calls) == 2


def test_mistral_named_work_target_object_is_flattened_for_runtime():
    http = FakeHttpClient(
        FakeResponse({
            "choices": [{"message": {"content": (
                '{"intent":{"capability":"work.assign",'
                '"arguments":{"target":{"type":"task",'
                '"target_name":"test1"},"audience":{'
                '"resolver":"explicit_mentions","mention_indices":[0]}}},'
                '"clarification":""}'
            )}}]
        })
    )
    translator = MistralCommandTranslator("secret", client=http)

    intent, error = translator.translate("assign task test1 to @Deval PB", ["deval@s.whatsapp.net"])

    assert error == ""
    assert intent["capability"] == "work.assign"
    assert intent["arguments"]["target_type"] == "task"
    assert intent["arguments"]["target_name"] == "test1"


def test_structured_work_target_is_not_misread_as_an_audience_resolver():
    assert target_expression({"target": {"type": "task", "id": 7}}) == ("", "")


def test_target_type_is_canonicalized_and_conflicts_fail_closed():
    assert _target_arguments({"target_type": " TASK ", "target_id": 7}) == {
        "target_type": "task",
        "target_id": 7,
    }
    assert _resolve_target_reference(
        None,
        {"target_type": "event", "target": {"type": "task", "target_name": "test1"}},
    ) is None


def test_structured_intent_compiles_through_existing_command_syntax():
    intent = {
        "capability": "labels.add",
        "arguments": {"collection": "team", "mention_indices": [0]},
    }
    with patch("features.natural_language._resolve_collection_name", return_value="team"):
        command = compile_intent(intent, "add @Person to team", object(), ["person@lid"])
    assert command == "!labels add team"


def test_generic_event_request_gets_safe_default_metadata():
    intent = {
        "capability": "work.create_event",
        "arguments": {"name": "pb oss work"},
    }

    normalized = _default_generic_event_fields(
        intent,
        "create an event called pb oss work and add tasks under it",
    )

    assert normalized["arguments"] == {
        "name": "pb oss work",
        "type": "organization",
        "category": "other",
    }


def test_known_program_event_keeps_its_documented_metadata():
    intent = {
        "capability": "work.create_event",
        "arguments": {"name": "Hacktoberfest work"},
    }

    normalized = _default_generic_event_fields(
        intent,
        "create a Hacktoberfest event called Hacktoberfest work",
    )

    assert normalized["arguments"]["type"] == "participation"
    assert normalized["arguments"]["category"] == "hacktoberfest"


def test_work_lifecycle_and_crud_capabilities_compile_to_legacy_handlers():
    with patch("features.natural_language._resolve_target_reference", side_effect=["event 4", "event 4", "task 7"]):
        assert compile_intent(
            {"capability": "work.set_lifecycle", "arguments": {"target_type": "event", "target_id": 4, "status": "completed"}},
            "complete event Zenith", object(), [],
        ) == "!set-status 4 | completed"
        assert compile_intent(
            {"capability": "work.update_event", "arguments": {"target_type": "event", "target_id": 4, "fields": {"name": "Renamed", "category": "hackathon"}}},
            "rename event", object(), [],
        ) == "!update-event 4 | name Renamed | category hackathon"
        assert compile_intent(
            {"capability": "work.delete_task", "arguments": {"target_type": "task", "target_id": 7}},
            "delete task 7", object(), [],
        ) == "!delete-task 7"


def test_task_compilation_preserves_explicit_event_link():
    command = compile_intent(
        {
            "capability": "work.create_task",
            "arguments": {"title": "Prepare slides", "event_id": 4},
        },
        "create task Prepare slides under event 4",
        object(),
        [],
    )
    assert command.endswith("| event 4")


def test_task_compilation_accepts_shared_event_target_shape():
    command = compile_intent(
        {
            "capability": "work.create_task",
            "arguments": {
                "title": "Prepare slides",
                "target_type": "event",
                "target_id": 4,
            },
        },
        "create task Prepare slides for event 4",
        object(),
        [],
    )
    assert command.endswith("| event 4")


def test_on_demand_reminder_compiles_named_event_or_task_target():
    intent = {
        "capability": "reminders.send",
        "arguments": {"target": "launch task"},
    }
    with patch(
        "features.natural_language._resolve_target_reference",
        return_value="task 9",
    ):
        assert compile_intent(intent, "reminder about launch task", object(), []) == (
            "!work reminders remind task 9"
        )


def test_media_post_title_resolves_to_numeric_id():
    intent = {
        "capability": "media.posted",
        "arguments": {"id": "Soso", "stage": "x"},
    }
    with patch(
        "db.media_store.MediaStore.read",
        return_value={
            "todo": [{"id": 12, "text": "Soso"}],
            "posted": [],
        },
    ):
        assert compile_intent(
            intent, "mark the Soso post done on X", object(), []
        ) == "!posted 12 twitter"


def test_media_post_stage_resolves_completion_wording_from_original_request():
    intent = {
        "capability": "media.posted",
        "arguments": {"id": "Soso", "stage": "done"},
    }
    with patch(
        "db.media_store.MediaStore.read",
        return_value={
            "todo": [{"id": 12, "text": "Soso"}],
            "posted": [],
        },
    ):
        assert compile_intent(
            intent,
            "@me mark the post for Soso todo as done for X",
            object(),
            [],
        ) == "!posted 12 twitter"


def test_ambiguous_media_post_title_is_not_resolved():
    intent = {
        "capability": "media.posted",
        "arguments": {"id": "Soso", "stage": "x"},
    }
    with patch(
        "db.media_store.MediaStore.read",
        return_value={
            "todo": [
                {"id": 12, "text": "Soso"},
                {"id": 13, "text": "Soso"},
            ],
            "posted": [],
        },
    ):
        assert compile_intent(intent, "mark the Soso post done on X", object(), []) is None


def test_natural_collection_names_are_normalized_to_stored_names():
    assert normalize_collection_name("2nd year") == "2nd-year"
    assert normalize_collection_name("  Backend / Maintainers! ") == "backend-maintainers"


def test_new_subgroup_is_created_when_name_does_not_exist_and_me_is_preserved():
    intent = {
        "capability": "collections.add",
        "arguments": {"collection": "2nd year", "mention_indices": [0, 1, 2]},
    }

    command = compile_intent(
        intent,
        "@me create a subgroup called 2nd year and add me and @Deval @Bibisha to it",
        object(),
        ["deval@lid", "bibisha@lid", "@me"],
    )

    assert command == "!add-subgroup 2nd-year | @me"


def test_existing_fuzzy_subgroup_wins_without_explicit_new_keyword():
    intent = {
        "capability": "collections.add",
        "arguments": {"collection": "lfx aplicants"},
    }
    with patch(
        "features.natural_language._resolve_collection_name",
        return_value="lfx-applicants",
    ):
        command = compile_intent(intent, "@me add me to lfx aplicants", object(), ["@me"])
    assert command == "!add-subgroup lfx-applicants | @me"


def test_explicit_new_keyword_overrides_similar_existing_subgroup():
    intent = {
        "capability": "collections.add",
        "arguments": {"collection": "lfx applicants"},
    }
    with patch(
        "features.natural_language._resolve_collection_name",
        return_value="lfx-applicants",
    ):
        command = compile_intent(
            intent,
            "@me create a new subgroup called lfx applicants",
            object(),
            ["@me"],
        )
    assert command == "!add-subgroup lfx-applicants"


def test_missing_label_is_created_with_normalized_name():
    intent = {
        "capability": "labels.add",
        "arguments": {"collection": "2nd year"},
    }
    command = compile_intent(intent, "@me add me to the 2nd year label", object(), ["@me"])
    assert command == "!labels add 2nd-year | @me"


def test_work_assignment_preserves_label_and_explicit_task_type():
    intent = {
        "capability": "work.assign",
        "arguments": {
            "target_type": "event",
            "target_id": 7,
            "collections": ["media"],
        },
    }
    with patch(
        "features.natural_language._resolve_collection_name",
        return_value="media",
    ):
        command = compile_intent(
            intent,
            "@me assign task 7 to label media",
            object(),
            [],
        )
    assert command == "!work assign event 7 | @media"


def test_intent_compilation_reports_the_missing_semantic_field():
    assert _intent_compile_error(
        {"capability": "work.overview", "arguments": {"target": 7}}
    ) == "work.overview requires argument target_type"
    assert _intent_compile_error(
        {"capability": "card.design", "arguments": {"name": "A"}}
    ) == "card.design requires argument body"


def test_scoped_progress_never_falls_back_to_global_report():
    with patch(
        "features.natural_language._resolve_target_reference",
        return_value=None,
    ):
        assert compile_intent(
            {
                "capability": "reports.progress",
                "arguments": {"target_type": "event", "target_name": "Missing"},
            },
            "show progress for Missing event",
            object(),
            [],
        ) is None


def test_target_word_order_is_normalized_at_the_shared_boundary():
    assert _typed_target_parts("event abc") == ("event", "abc")
    assert _typed_target_parts("abc event") == ("event", "abc")
    assert _target_arguments({"target": "event abc"}) == {
        "target": "event abc",
        "target_type": "event",
        "target_name": "abc",
    }
    assert _target_arguments({"target": "abc event"}) == {
        "target": "abc event",
        "target_type": "event",
        "target_name": "abc",
    }


def test_structured_named_target_is_normalized_by_the_shared_target_parser():
    assert _target_arguments({
        "target": {"type": "task", "target_name": "test1"},
    }) == {
        "target": {"type": "task", "target_name": "test1"},
        "target_type": "task",
        "target_name": "test1",
    }


def test_ambiguous_collection_names_fail_closed():
    with patch(
        "db.subgroup_store.SubgroupStore.read",
        return_value={"back-end": [], "back_end": []},
    ):
        factory = MagicMock()
        assert _resolve_collection_name(factory, "backend") is None
        assert _resolve_collection_names(factory, ["back-end", "back_end"]) == [
            "back-end", "back_end"
        ]


def test_model_json_fences_are_parsed_without_guessing_a_command():
    http = FakeHttpClient(
        FakeResponse({
            "choices": [{"message": {"content": '```json\n{"command":"!my"}\n```'}}]
        })
    )
    translator = MistralCommandTranslator("secret", client=http)

    command, error = translator.translate("show my work", [])

    assert command == "!my"
    assert error == ""


def test_named_task_target_is_scoped_to_parent_event_from_plain_language():
    arguments = _target_arguments(
        {"target_type": "task", "target_name": "website"},
        "assign website task under pb work event to @Bibisha and @Deval PB",
    )

    assert arguments["target_type"] == "task"
    assert arguments["target_name"] == "website"
    assert arguments["parent_event_name"] == "pb work"


def test_task_first_assignment_wording_extracts_named_target():
    assert _natural_work_target("assign task test1 to @Deval PB") == ("task", "test1")
    assert _target_arguments({}, "assign task test1 to @Deval PB") == {
        "target_type": "task",
        "target_name": "test1",
    }


def test_duplicate_task_title_resolves_when_parent_event_is_named():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.auth import upsert_user
    from db.event_store import EventStore
    from db.models import Base
    from db.task_store import TaskStore

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    creator = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    events = EventStore(factory)
    first = events.create_event(name="first", type="organization", status="active")
    second = events.create_event(name="pb work", type="organization", status="active")
    tasks = TaskStore(factory)
    tasks.create("website", creator.jid, event_id=first["id"])
    selected = tasks.create("website", creator.jid, event_id=second["id"])

    assert _resolve_target_reference(
        factory,
        {"target_type": "task", "target_name": "website"},
    ) is None
    assert _resolve_target_reference(
        factory,
        {
            "target_type": "task",
            "target_name": "website",
            "parent_event_name": "pb work",
        },
    ) == f"task {selected.id}"


def test_two_different_task_names_can_fuzzy_collapse_onto_the_same_task():
    """Documents the underlying leniency that makes the plan-collision guard
    necessary: a typo'd/nonexistent name ("how") with no close second
    candidate can fuzzy-match the same task as a genuinely different,
    correctly-named one ("hw") -- observed live in production when a
    compound assign silently applied both mentions to task 16 instead of
    two distinct tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db.auth import upsert_user
    from db.event_store import EventStore
    from db.models import Base
    from db.task_store import TaskStore

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    creator = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    event = EventStore(factory).create_event(name="bbhh", type="organization", status="active")
    hw = TaskStore(factory).create("hw", creator.jid, event_id=event["id"])
    TaskStore(factory).create("ww", creator.jid, event_id=event["id"])

    assert _resolve_target_reference(
        factory, {"target_type": "task", "target_name": "hw", "parent_event_name": "bbhh"},
    ) == f"task {hw.id}"
    assert _resolve_target_reference(
        factory, {"target_type": "task", "target_name": "how", "parent_event_name": "bbhh"},
    ) == f"task {hw.id}"


def test_plan_target_collision_error_rejects_two_names_mapping_to_one_task():
    tracker: dict = {}
    assert _plan_target_collision_error(tracker, "task 16", "hw") is None
    error = _plan_target_collision_error(tracker, "task 16", "how")
    assert error is not None
    assert "hw" in error and "how" in error and "task 16" in error


def test_plan_target_collision_error_allows_repeat_reference_by_the_same_name():
    tracker: dict = {}
    assert _plan_target_collision_error(tracker, "task 16", "hw") is None
    assert _plan_target_collision_error(tracker, "task 16", "hw") is None
    assert _plan_target_collision_error(tracker, "task 16", "HW") is None


def test_collections_tag_audience_is_always_derived_from_its_own_collection():
    """collections.tag's audience is fully determined by which subgroup it
    names -- the model sometimes independently picks a different resolver
    (current_chat_members, an unresolved explicit_mentions) even after
    correctly choosing this capability, which would silently notify the
    wrong people instead of the named subgroup's members."""
    for wrong_audience in (
        {"resolver": "current_chat_members"},
        {"resolver": "explicit_mentions"},
        None,
    ):
        arguments = {"collection": "backend"}
        if wrong_audience is not None:
            arguments["audience"] = wrong_audience
        validated = validate_intent({"capability": "collections.tag", "arguments": arguments})
        assert validated["arguments"]["audience"] == {
            "resolver": "collection_members", "value": "backend",
        }


def test_subgroup_tag_wording_cannot_be_compiled_as_removal_or_info():
    repaired = _repair_collection_tag_intent(
        {
            "capability": "collections.remove",
            "arguments": {"collection": "abc"},
        },
        "tag everyone in subgroup abc",
        object(),
    )

    assert repaired == {
        "capability": "collections.tag",
        "arguments": {
            "collection": "abc",
            "audience": {
                "resolver": "collection_members",
                "value": "abc",
            },
        },
    }


def test_notify_phrasing_synonyms_repair_to_collection_tag():
    """"shout out to backend" and "give ops a heads up" mean the same thing
    as "tag backend" / "notify ops" -- the repair heuristic that rescues a
    misclassified collections.add/remove/info must recognize them too, not
    just the literal words tag/ping/mention/notify."""
    for text in (
        "shout out to subgroup backend, the deploy is done",
        "give the group backend a heads up about the outage",
        "loop in subgroup backend about the release",
    ):
        repaired = _repair_collection_tag_intent(
            {"capability": "collections.info", "arguments": {"collection": "backend"}},
            text,
            object(),
        )
        assert repaired["capability"] == "collections.tag", text


def test_bare_subgroup_mention_keeps_legacy_tag_semantics():
    repaired = _repair_collection_tag_intent(
        {
            "capability": "collections.info",
            "arguments": {},
        },
        "@abc",
        object(),
    )

    assert repaired["capability"] == "collections.tag"
    assert repaired["arguments"]["collection"] == "abc"


def test_subgroup_tag_is_allowed_in_compound_plans():
    from features.transaction import TRANSACTIONAL_PLAN_CAPABILITIES

    assert "collections.tag" in TRANSACTIONAL_PLAN_CAPABILITIES
    assert "media.posted" in TRANSACTIONAL_PLAN_CAPABILITIES
    assert "media.unposted" in TRANSACTIONAL_PLAN_CAPABILITIES


def test_media_handler_uses_plan_transaction_session_factory():
    from features.media import register as register_media

    default_factory = object()
    transaction_factory = object()
    client = MagicMock()
    message = make_message("!todo")
    message.Info.MessageSource.Chat.User = "123"
    captured = []

    async def capture_store(_client, _message, store):
        captured.append(store.session_factory)

    handler = register_media(
        client,
        {"media_group_id": "123@g.us", "db_session_factory": default_factory},
    )
    message._pbbot_session_factory = transaction_factory
    with patch("features.media._handle_media_command", side_effect=capture_store):
        handler(client, message)

    assert captured == [transaction_factory]


def test_media_handler_accepts_commands_in_pbbot_group():
    from features.media import register as register_media

    default_factory = object()
    client = MagicMock()
    message = make_message("!todo")
    message.Info.MessageSource.Chat.User = "123"
    message._pbbot_session_factory = default_factory
    captured = []

    async def capture_store(_client, _message, store):
        captured.append(store.session_factory)

    handler = register_media(
        client,
        {
            "media_group_id": "456@g.us",
            "pbbot_group_id": "123@g.us",
            "db_session_factory": default_factory,
        },
    )
    with patch("features.media._handle_media_command", side_effect=capture_store):
        handler(client, message)

    assert captured == [default_factory]


def test_media_validation_failure_aborts_a_compound_transaction():
    import asyncio
    from features.media import _handle_media_command

    marker = MagicMock()
    store = MagicMock()
    store.session_factory = marker
    message = make_message("!posted 1 unsupported")

    asyncio.run(_handle_media_command(MagicMock(), message, store))

    marker.mark_failed.assert_called_once()


def test_explicit_work_target_text_repairs_misplaced_model_fields():
    assert _target_arguments(
        {"target_type": "task", "target_name": "8"},
        "@me assign task 8 to @Shuvam",
    ) == {"target_type": "task", "target_id": "8"}
    assert _target_arguments(
        {"target_type": "task", "target_name": "task"},
        "@me assign task tell result to @Shuvam",
    ) == {"target_type": "task", "target_name": "tell result"}
    assert _target_arguments(
        {"target_type": "task", "target_name": "task"},
        "@me assign task named tell result to @Shuvam",
    ) == {"target_type": "task", "target_name": "tell result"}
    assert _target_arguments(
        {"target_name": "task"},
        "@me assign task tell result to @Shuvam",
    ) == {"target_type": "task", "target_name": "tell result"}


def test_work_assignment_target_type_is_recovered_when_model_only_returns_id():
    assert _target_arguments(
        {"target_id": 6},
        "@me assign task raise pr to @Bibisha",
    ) == {"target_id": 6, "target_type": "task"}


def test_admin_target_suffix_names_a_mentioned_other_person():
    """The ' | @me' suffix compile_intent used to emit for self-reference
    has a pipe that features/work.py's _target() parser never recognizes as
    a mention token -- it silently falls through to the sender default.
    This is the ONLY path that can actually name someone else, and it must
    produce a bare space-separated '@<jid>' token."""
    arguments = {
        "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
    }
    assert _admin_target_suffix(arguments, ["bob@s.whatsapp.net"]) == " @bob@s.whatsapp.net"


def test_admin_target_suffix_is_empty_without_an_explicit_mentions_audience():
    assert _admin_target_suffix({}, ["bob@s.whatsapp.net"]) == ""
    assert _admin_target_suffix(
        {"audience": {"resolver": "current_chat_members"}}, ["bob@s.whatsapp.net"]
    ) == ""


def test_admin_target_suffix_fails_closed_on_an_unavailable_index():
    arguments = {
        "audience": {"resolver": "explicit_mentions", "mention_indices": [5]},
    }
    assert _admin_target_suffix(arguments, ["bob@s.whatsapp.net"]) == ""


def test_compile_intent_names_the_mentioned_person_for_work_update():
    """"update status for task 6 for @Bob to in progress" must compile to a
    command naming Bob specifically, not silently fall back to the sender
    or an unresolved target. Uses the same intent shape validate_intent
    requires and real Mistral responses actually produce -- a typed target
    dict, not bare target_type/target_id -- since validate_intent only
    unlocks the audience field for these capabilities when it recognizes
    that shape (see _TYPED_TARGET_CAPABILITIES)."""
    intent = validate_intent({
        "capability": "work.update",
        "arguments": {
            "target": {"target_type": "task", "target_id": 6},
            "field": "status",
            "value": "in_progress",
            "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
        },
    })
    assert intent is not None
    command = compile_intent(
        intent,
        "update status for task 6 for @Bob to in progress",
        object(),
        ["bob@s.whatsapp.net"],
    )
    assert command == "!work start task 6 @bob@s.whatsapp.net"


def test_compile_intent_names_the_mentioned_person_for_work_complete():
    intent = validate_intent({
        "capability": "work.complete",
        "arguments": {
            "target": {"target_type": "task", "target_id": 6},
            "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
        },
    })
    assert intent is not None
    command = compile_intent(
        intent, "mark task 6 done for @Bob", object(), ["bob@s.whatsapp.net"]
    )
    assert command == "!work complete task 6 @bob@s.whatsapp.net"


def test_bare_work_title_wins_over_model_numeric_name():
    assert _target_arguments(
        {"target_name": "9"},
        "@me assign fuck off to @Bibisha",
    ) == {"target_name": "fuck off"}


def test_explicit_bot_assignment_target_is_allowed():
    client = MagicMock()
    message = make_message("@me assign task 6 to @Bibisha")
    intent = {
        "capability": "work.assign",
        "arguments": {
            "target_type": "task",
            "target_id": 6,
            "audience": {"resolver": "explicit_mentions"},
        },
    }

    members, error = _resolve_runtime_target_scope(
        client,
        message,
        intent,
        {"bot@s.whatsapp.net"},
        visible_mentions=["bot@s.whatsapp.net"],
    )

    assert members == ["bot@s.whatsapp.net"]
    assert error is None


def test_work_handler_uses_runtime_resolved_mentions():
    from features.work import _assign_targets

    client = MagicMock()
    message = make_message("!work assign task 6")
    message._pbbot_runtime_mentions = ["bot@s.whatsapp.net"]

    with patch("features.work.SubgroupStore.read", return_value={}):
        targets, aliases = _assign_targets(
            client,
            message.Info.MessageSource.Chat,
            message,
            "",
            None,
            MagicMock(),
        )

    assert targets == ["bot@s.whatsapp.net"]
    assert aliases == {}


def test_group_message_quoting_a_reminder_is_processed_without_bot_mention():
    """A reply that quotes a tracked group reminder must be treated as an
    address to the bot on its own -- requiring "@bot" in addition to
    quoting the reminder is not how anyone actually replies to one."""
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("I finished task 5")  # no @me, no bot mention
    dispatch = MagicMock()
    intent = {"capability": "work.complete", "arguments": {"target": 5}}

    with patch("features.natural_language.is_bot_mentioned", return_value=False), \
         patch("features.neonize_policy.is_reminder_reply", return_value=True), \
         patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(intent, "")) as translate:
        handler = register(client, {"mistral_api_key": "secret"})
        handler(client, message, dispatch)

    translate.assert_called_once()


def test_group_message_without_bot_mention_or_reminder_reply_is_ignored():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("just chatting, not a command")
    dispatch = MagicMock()

    with patch("features.natural_language.is_bot_mentioned", return_value=False), \
         patch("features.neonize_policy.is_reminder_reply", return_value=False), \
         patch.object(MistralCommandTranslator, "translate") as translate:
        handler = register(client, {"mistral_api_key": "secret"})
        result = handler(client, message, dispatch)

    assert result is False
    translate.assert_not_called()


def test_legacy_mutating_command_is_rejected_before_dispatch():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me create a label called media")
    dispatch = MagicMock()

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), patch.object(
        MistralCommandTranslator,
        "translate",
        return_value=("!labels add media", ""),
    ) as translate:
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    translate.assert_called_once()
    assert translate.call_args.args[0] == "create a label called media"
    dispatch.assert_not_called()
    assert "safely resolve" in str(client.send_message.call_args)


def test_canonical_audience_object_resolves_current_chat_members():
    client = MagicMock()
    message = make_message("@me add everyone here to backend")
    intent = {
        "capability": "collections.add",
        "arguments": {
            "collection": "backend",
            "audience": {"resolver": "current_chat_members"},
        },
    }
    with patch(
        "features.community_tag.get_group_member_jids",
        return_value=["111@s.whatsapp.net", "bot@s.whatsapp.net"],
    ):
        members, error = _resolve_runtime_target_scope(
            client,
            message,
            intent,
            {"bot@s.whatsapp.net"},
            visible_mentions=[],
        )

    assert error is None
    assert members == ["111@s.whatsapp.net"]


def test_required_target_cannot_reach_compilation_unresolved():
    from features.nl_runtime import TargetResolution, validate_execution_ready

    intent = {
        "capability": "collections.add",
        "arguments": {"collection": "backend"},
    }
    error = validate_execution_ready(intent, TargetResolution(), [])

    assert error == "I couldn't identify who this operation should affect."


def test_unresolved_required_target_is_stopped_before_legacy_dispatch():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me create a subgroup for the people")
    dispatch = MagicMock()
    intent = {
        "capability": "collections.add",
        "arguments": {"collection": "people"},
    }

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(intent, "")), \
         patch.object(MistralCommandTranslator, "repair_missing_target", return_value=None):
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    assert any(
        "identify who this operation should affect" in str(call)
        for call in client.send_message.call_args_list
    )


def test_group_audience_repair_reaches_dispatch_with_resolved_members():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message(
        '@me I want to create a subgroup to assign everyone in this group '
        'in it and call it "everyone"'
    )
    dispatch = MagicMock()
    incomplete = {
        "capability": "collections.add",
        "arguments": {"collection": "everyone"},
    }
    repaired = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(incomplete, "")), \
         patch.object(MistralCommandTranslator, "repair_missing_target", return_value=repaired), \
         patch(
             "features.community_tag.get_group_member_jids",
             return_value=["111@s.whatsapp.net", "222@s.whatsapp.net"],
         ), patch(
             "features.natural_language._execute_direct_operation",
             return_value=True,
         ) as execute:
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    execute.assert_called_once()
    assert execute.call_args.args[2] == repaired
    assert execute.call_args.args[3] == ["111@s.whatsapp.net", "222@s.whatsapp.net"]


def test_compound_two_target_request_becomes_a_two_step_plan_not_one():
    """"assign task 5 to @Alice and assign task 6 to @Bob" must never
    silently execute only one of the two assignments just because the model
    collapsed both clauses into a single intent."""
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me assign task 5 to @Alice and assign task 6 to @Bob")
    dispatch = MagicMock()
    collapsed = {
        "capability": "work.assign",
        "arguments": {
            "target_type": "task",
            "target_id": 5,
            "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
        },
    }
    repaired_plan = [
        {
            "step_id": "assign5",
            "capability": "work.assign",
            "arguments": {
                "target_type": "task",
                "target_id": 5,
                "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
            },
        },
        {
            "step_id": "assign6",
            "capability": "work.assign",
            "arguments": {
                "target_type": "task",
                "target_id": 6,
                "audience": {"resolver": "explicit_mentions", "mention_indices": [1]},
            },
        },
    ]
    factory = MagicMock()
    actor = SimpleNamespace(role="admin")

    with patch(
        "features.natural_language._get_mentioned_jids",
        return_value=["alice@s.whatsapp.net", "bob@s.whatsapp.net"],
    ), patch.object(
        MistralCommandTranslator, "translate", return_value=(collapsed, "")
    ), patch.object(
        MistralCommandTranslator, "repair_plan", return_value=repaired_plan
    ) as repair, patch("db.auth.gate", return_value=actor), patch(
        "features.natural_language._execute_direct_operation",
        return_value={"ok": True},
    ) as execute:
        handler = register(
            client,
            {"mistral_api_key": "secret", "db_session_factory": factory},
        )
        assert handler(client, message, dispatch) is True

    repair.assert_called_once()
    dispatch.assert_not_called()
    assert execute.call_count == 2
    target_ids = {call.args[2]["arguments"]["target_id"] for call in execute.call_args_list}
    assert target_ids == {5, 6}


def test_compound_two_target_request_warns_when_repair_fails_instead_of_running_one():
    """If the repair pass can't split the request either, the bot must say
    so -- not silently run just the one target it happened to keep."""
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me complete task 5 and complete task 6")
    dispatch = MagicMock()
    collapsed = {
        "capability": "work.complete",
        "arguments": {"target_type": "task", "target_id": 5},
    }

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(collapsed, "")), \
         patch.object(MistralCommandTranslator, "repair_plan", return_value=None):
        handler = register(
            client,
            {"mistral_api_key": "secret", "db_session_factory": MagicMock()},
        )
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    assert any(
        "more than one target" in str(call)
        for call in client.send_message.call_args_list
    )


def test_same_target_two_actions_becomes_a_two_step_plan_not_one():
    """"complete task 5 and reassign it to @Bob" must never silently run
    only the completion and drop the reassignment."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from db.auth import upsert_user
    from db.models import Base
    from db.task_store import TaskStore

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    admin = upsert_user(factory, "admin@s.whatsapp.net", role="admin")
    task = TaskStore(factory).create("Fix the bug", admin.jid)

    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message(
        f"@me complete task {task.id} and reassign it to @Bob", sender=admin.jid
    )
    dispatch = MagicMock()
    collapsed = {
        "capability": "work.complete",
        "arguments": {"target_type": "task", "target_id": task.id},
    }
    repaired_plan = [
        {
            "step_id": "complete",
            "capability": "work.complete",
            "arguments": {"target_type": "task", "target_id": task.id},
        },
        {
            "step_id": "reassign",
            "capability": "work.assign",
            "arguments": {
                "target_type": "task",
                "target_id": task.id,
                "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
            },
        },
    ]

    with patch(
        "features.natural_language._get_mentioned_jids",
        return_value=["bob@s.whatsapp.net"],
    ), patch.object(
        MistralCommandTranslator, "translate", return_value=(collapsed, "")
    ), patch.object(
        MistralCommandTranslator, "repair_plan", return_value=repaired_plan
    ) as repair, patch(
        "features.natural_language._execute_direct_operation",
        return_value={"ok": True},
    ) as execute:
        handler = register(
            client,
            {"mistral_api_key": "secret", "db_session_factory": factory},
        )
        assert handler(client, message, dispatch) is True

    repair.assert_called_once()
    # work.complete is not a "direct" capability, so it compiles to the
    # legacy bang-command and dispatches through the normal command router;
    # work.assign is direct and executes through the domain operation
    # registry. What matters is that BOTH steps ran -- neither silently
    # dropped -- regardless of which path each one takes.
    assert execute.call_count == 1
    assert execute.call_args.args[2]["capability"] == "work.assign"
    assert dispatch.call_count == 1


def test_exact_group_request_executes_direct_subgroup_operation():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message(
        '@me I want to create a subgroup to assign everyone in this group '
        'in it and call it "everyone"'
    )
    dispatch = MagicMock()
    incomplete = {
        "capability": "collections.add",
        "arguments": {"collection": "everyone"},
    }
    repaired = {
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }
    factory = MagicMock()
    actor = SimpleNamespace(role="admin")

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(incomplete, "")), \
         patch.object(MistralCommandTranslator, "repair_missing_target", return_value=repaired), \
         patch(
             "features.community_tag.get_group_member_jids",
             return_value=["111@s.whatsapp.net", "222@s.whatsapp.net"],
         ), patch(
             "db.auth.gate",
             return_value=actor,
         ), patch(
             "features.natural_language._resolve_or_create_collection_name",
             return_value="everyone",
         ), patch(
             "features.subgroups.add_subgroup_members",
             return_value=(2, 2),
         ) as add_members:
        handler = register(
            client,
            {"mistral_api_key": "secret", "db_session_factory": factory},
        )
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    add_members.assert_called_once()
    assert add_members.call_args.args[1:] == (
        "everyone",
        ["111@s.whatsapp.net", "222@s.whatsapp.net"],
    )
    assert "Added 2 member(s)" in str(client.send_message.call_args)


def test_invalid_audience_resolver_is_rejected():
    from features.natural_language import validate_intent

    assert validate_intent({
        "capability": "collections.add",
        "arguments": {"collection": "everyone", "audience": {"resolver": "all_users"}},
    }) is None
    assert validate_intent({
        "capability": "work.assign",
        "arguments": {"target": "task 7", "target_type": "task", "target_id": 7},
    }) is not None


def test_translator_accepts_a_bounded_multi_step_semantic_plan():
    http = FakeHttpClient(FakeResponse({
        "choices": [{"message": {"content": (
            '{"plan":[{"capability":"collections.add",'
            '"arguments":{"collection":"everyone",'
            '"audience":{"resolver":"current_chat_members"}}}],'
            '"clarification":""}'
        )}}]
    }))
    translator = MistralCommandTranslator("secret", client=http)

    plan, clarification = translator.translate("put everyone here in a subgroup", [])

    assert plan == {"plan": [{
        "capability": "collections.add",
        "arguments": {
            "collection": "everyone",
            "audience": {"resolver": "current_chat_members"},
        },
    }]}
    assert clarification == ""
    assert validate_plan(plan["plan"]) is not None


def test_collection_member_audience_resolves_from_persisted_store():
    client = MagicMock()
    message = make_message("@me assign it to backend")
    intent = {
        "capability": "work.assign",
        "arguments": {
            "audience": {"resolver": "collection_members", "value": "backend"},
        },
    }
    factory = MagicMock()
    with patch(
        "features.natural_language._resolve_collection_name",
        return_value="backend",
    ), patch(
        "db.subgroup_store.SubgroupStore.read",
        return_value={"backend": ["111@s.whatsapp.net"]},
    ):
        members, error = _resolve_runtime_target_scope(
            client, message, intent, set(), factory
        )

    assert error is None
    assert members == ["111@s.whatsapp.net"]


def test_missing_audience_is_sent_through_generic_repair_pass():
    intent = {
        "capability": "collections.add",
        "arguments": {"collection": "everyone"},
    }
    assert _needs_target_repair(
        intent,
        "create a subgroup to assign everyone in this group in it",
        [],
    ) is True
    assert _needs_target_repair(
        {
            **intent,
            "arguments": {
                **intent["arguments"],
                "audience": {"resolver": "explicit_mentions"},
            },
        },
        "create a subgroup to assign everyone in this group in it",
        [],
    ) is True

    http = FakeHttpClient(FakeResponse({
        "choices": [{"message": {"content": (
            '{"intent":{"capability":"collections.add","arguments":'
            '{"collection":"everyone","audience":{"resolver":"current_chat_members"}}},'
            '"clarification":""}'
        )}}]
    }))
    translator = MistralCommandTranslator("secret", client=http)
    repaired = translator.repair_missing_target(
        "create a subgroup for everyone here",
        intent,
        [],
    )

    assert repaired["arguments"]["audience"] == {"resolver": "current_chat_members"}
    assert len(http.calls) == 1


def test_open_ended_card_design_keeps_original_template_without_style_request():
    intent = {
        "capability": "card.design",
        "arguments": {
            "base_template": "hackathon",
            "name": "Zodiak",
            "text": "For successfully failing to save your asses at PBCTF 5.0",
            "title": "Congratulations, Zodiak!",
            "accent": "#a855f7",
            "logo_urls": ["https://example.com/logo.svg"],
            "highlight_terms": ["PBCTF 5.0"],
            "tone": "sarcastic",
        },
    }

    command, design = compile_card_design(
        intent,
        "create a card for Zodiak using https://example.com/logo.svg as the logo",
    )

    assert command == (
        "!card hackathon | Zodiak | For successfully failing to save your asses at PBCTF 5.0 "
        "| https://example.com/logo.svg"
    )
    assert design is None


def test_open_ended_card_design_applies_spec_when_style_is_explicitly_requested():
    intent = {
        "capability": "card.design",
        "arguments": {
            "base_template": "hackathon",
            "name": "Zodiak",
            "text": "For successfully failing to save your asses at PBCTF 5.0",
            "title": "Congratulations, Zodiak!",
            "accent": "#a855f7",
            "highlight_terms": ["PBCTF 5.0"],
            "tone": "sarcastic",
        },
    }

    command, design = compile_card_design(
        intent,
        "create a sarcastic purple style hackathon card for Zodiak",
    )

    assert command.startswith("!card hackathon | Zodiak | ")
    assert design["base_template"] == "hackathon"
    assert design["accent"] == "#A855F7"
    assert design["highlight_terms"] == ["PBCTF 5.0"]
    assert design["tone"] == "sarcastic"


def test_dedicated_card_designer_preserves_semantic_brief_fields():
    http = FakeHttpClient(FakeResponse({
        "choices": [{"message": {"content": (
            '{"base_template":"custom","name":"Zodiak",'
            '"occasion":"PBCTF 5.0 failure","tone":"sarcastic",'
            '"headline":"Congratulations on the Failure",'
            '"body":"For failing to save the team at PBCTF 5.0",'
            '"accent":"#FF5C8A","pill":"PBCTF 5.0",'
            '"logo_urls":[],"highlight_terms":["failure"]}'
        )}}]
    }))
    from features.natural_language import MistralCardDesigner

    designer = MistralCardDesigner("secret", client=http)
    brief = designer.design("make a sarcastic card for Zodiak")

    assert brief["capability"] == "card.design"
    assert brief["arguments"]["tone"] == "sarcastic"
    assert brief["arguments"]["headline"] == "Congratulations on the Failure"
    assert http.calls[0][1]["json"]["model"] == "mistral-medium-3-5"


def test_explicit_logo_url_survives_when_design_model_omits_logo_urls():
    intent = {
        "capability": "card.design",
        "arguments": {
            "name": "Zodiak",
            "body": "For failing to save the team",
            "tone": "sarcastic",
        },
    }

    command, design = compile_card_design(
        intent,
        "make a sarcastic card for Zodiak; use https://example.com/logo.svg as the logo",
    )

    assert command.endswith(" | https://example.com/logo.svg")
    assert design["logo_url"] == "https://example.com/logo.svg"


def test_unknown_design_family_falls_back_to_custom_instead_of_help():
    intent = {
        "capability": "card.design",
        "arguments": {
            "base_template": "congratulations",
            "name": "Zodiak",
            "text": "PBCTF 5.0 legend",
        },
    }

    command, design = compile_card_design(intent, "make a congratulations card")

    assert command.startswith("!card custom | Zodiak | PBCTF 5.0 legend")
    assert design is None


def test_misclassified_unknown_card_type_is_promoted_to_design_mode():
    intent = {
        "capability": "card.create",
        "arguments": {
            "type": "congratulations",
            "name": "Zodiak",
            "text": "PBCTF 5.0 legend",
        },
    }

    command, design = compile_card_design(intent, "make a congratulations card")

    assert command == "!card custom | Zodiak | PBCTF 5.0 legend"
    assert design is None


def test_invalid_or_missing_model_command_is_rejected_without_guessing():
    http = FakeHttpClient(
        FakeResponse({"choices": [{"message": {"content": '{"command":null,"clarification":"Please clarify"}'}}]})
    )
    translator = MistralCommandTranslator("secret", client=http)

    command, clarification = translator.translate("make the thing", [])

    assert command is None
    assert clarification == ""


def test_translation_failure_does_not_reenter_dispatch_with_a_guessed_command():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me please do something", "member@s.whatsapp.net")
    dispatch = MagicMock()

    with patch.object(
        MistralCommandTranslator,
        "translate",
        side_effect=RuntimeError("Mistral unavailable"),
    ):
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    assert "safely resolve" in str(client.send_message.call_args)


def test_scoped_capability_is_reviewed_when_model_drops_named_entity():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me what are the tasks left under Zenith 27")
    dispatch = MagicMock()
    candidate = {
        "capability": "work.overview",
        "arguments": {"status": "pending"},
    }
    repaired = {
        "capability": "work.list_event_tasks",
        "arguments": {"target_type": "event", "target_id": 10},
    }
    with patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch("features.natural_language._named_entity_candidates", return_value=[{
             "type": "event", "id": 10, "name": "Zenith 27", "category": "other",
         }]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(candidate, "")), \
         patch.object(MistralCommandTranslator, "repair_intent", return_value=repaired), \
         patch("features.natural_language._execute_direct_operation", return_value={"tasks": [], "task_count": 0}) as execute:
        handler = register(
            client,
            {"mistral_api_key": "secret", "db_session_factory": MagicMock()},
        )
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    execute.assert_called_once()
    assert execute.call_args.args[2] == repaired


def test_local_entity_match_survives_failed_model_scope_repair():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me show status for abc event")
    dispatch = MagicMock()
    candidate = {
        "type": "event", "id": 7, "name": "abc", "category": "other",
    }
    intent = {"capability": "work.status", "arguments": {}}

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), \
         patch("features.natural_language._named_entity_candidates", return_value=[candidate]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(intent, "")), \
         patch.object(MistralCommandTranslator, "repair_intent", return_value=None), \
         patch("features.natural_language._resolve_target_reference", return_value="event 7"):
        handler = register(
            client,
            {"mistral_api_key": "secret", "db_session_factory": MagicMock()},
        )
        assert handler(client, message, dispatch) is True

    translated = dispatch.call_args.args[0]
    assert translated.Message.conversation == "!work status event 7"


def test_task_step_inherits_unique_event_created_by_same_plan():
    intent = {
        "capability": "work.create_task",
        "arguments": {"title": "Raise funds"},
    }
    linked = _inherit_plan_context(intent, {"event": {"event_id": 10}})
    assert linked["arguments"]["event_id"] == 10


def test_full_message_target_extraction_does_not_override_explicit_step_target():
    arguments = _target_arguments(
        {"target_type": "task", "target_id": 13},
        "assign task 12 and 13 to @Deval PB",
    )
    assert arguments["target_id"] == 13


def test_plan_completeness_detects_multiple_collections_in_one_relation():
    plan = [{
        "step_id": "add",
        "capability": "labels.add",
        "arguments": {
            "collection": "congr",
            "audience": {
                "resolver": "collection_members",
                "value": "2nd-yearsand careers-page",
            },
        },
    }]
    with patch(
        "features.natural_language._named_collection_candidates",
        return_value=[
            {"name": "2nd-years", "score": 0.9},
            {"name": "careers-page", "score": 0.9},
        ],
    ):
        issue = _plan_completeness_issue(plan, "add both collections", object())
    assert "multiple existing collections" in issue


def test_intent_completeness_detects_a_dropped_second_numeric_target():
    """"assign task 5 to X and task 6 to Y" collapsing into a single intent
    that only resolved task 5 must be flagged -- the model dropped task 6
    entirely rather than the runtime ever seeing a 2-step plan."""
    intent = {
        "capability": "work.assign",
        "arguments": {"target_type": "task", "target_id": 5},
    }
    issue = _intent_completeness_issue(
        intent, "assign task 5 to @Alice and assign task 6 to @Bob"
    )
    assert issue is not None
    assert "5" in issue and "6" in issue


def test_intent_completeness_ignores_a_single_target():
    intent = {
        "capability": "work.assign",
        "arguments": {"target_type": "task", "target_id": 5},
    }
    assert _intent_completeness_issue(intent, "assign task 5 to @Alice") is None


def test_intent_completeness_ignores_the_same_number_repeated():
    intent = {
        "capability": "work.status",
        "arguments": {"target_type": "task", "target_id": 5},
    }
    assert _intent_completeness_issue(
        intent, "what's the status of task 5? I mean task 5 specifically."
    ) is None


def test_intent_completeness_ignores_unrelated_capabilities():
    # work.create_task has no single numeric target to lose track of.
    intent = {"capability": "work.create_task", "arguments": {"title": "task 5 vs task 6"}}
    assert _intent_completeness_issue(intent, "task 5 vs task 6") is None


def test_intent_completeness_detects_a_dropped_second_quoted_title():
    """"mark 'Sprint Planning' as done and mark 'Sprint Review' as done"
    collapsing into a single intent for only one quoted title must be
    flagged -- there's no numeric ID here for the digit-based check to see."""
    intent = {
        "capability": "work.set_lifecycle",
        "arguments": {"target": "Sprint Planning", "status": "completed"},
    }
    issue = _intent_completeness_issue(
        intent, "mark 'Sprint Planning' as done and mark 'Sprint Review' as done"
    )
    assert issue is not None
    assert "sprint planning" in issue and "sprint review" in issue


def test_intent_completeness_detects_a_dropped_second_quoted_assignee_target():
    intent = {
        "capability": "work.assign",
        "arguments": {
            "target_type": "event",
            "target_name": "Website Redesign",
            "audience": {"resolver": "explicit_mentions", "mention_indices": [0]},
        },
    }
    issue = _intent_completeness_issue(
        intent, "assign 'Website Redesign' to @Alice and 'API Migration' to @Bob"
    )
    assert issue is not None


def test_intent_completeness_ignores_a_single_quoted_title():
    intent = {
        "capability": "work.set_lifecycle",
        "arguments": {"target": "Sprint Planning", "status": "completed"},
    }
    assert _intent_completeness_issue(intent, "mark 'Sprint Planning' as done") is None


def test_intent_completeness_ignores_the_same_quoted_title_repeated():
    intent = {
        "capability": "work.set_lifecycle",
        "arguments": {"target": "Sprint Planning", "status": "completed"},
    }
    assert _intent_completeness_issue(
        intent, "mark 'Sprint Planning' as done, I really mean 'Sprint Planning'"
    ) is None


def test_intent_completeness_detects_mixed_numeric_and_quoted_targets():
    """One numeric target plus one quoted target is still two distinct
    targets -- neither the numeric-only nor the quoted-only check alone
    would catch this, since each individually only sees one match."""
    intent = {"capability": "work.complete", "arguments": {"target": 5}}
    issue = _intent_completeness_issue(
        intent, "complete task 5 and complete the 'onboarding doc' task"
    )
    assert issue is not None


def test_intent_completeness_detects_two_actions_on_the_same_target():
    """"complete task 5 and reassign it to @Bob" collapsing to just the
    completion, with the reassignment silently dropped, must be flagged --
    there's only one numeric target here, so the target-multiplicity checks
    can't see this; it needs the action-multiplicity check."""
    intent = {"capability": "work.complete", "arguments": {"target": 5}}
    issue = _intent_completeness_issue(intent, "complete task 5 and reassign it to @Bob")
    assert issue is not None
    assert "complete" in issue and "assign" in issue


def test_intent_completeness_detects_unassign_then_delete():
    intent = {
        "capability": "work.unassign",
        "arguments": {"target": {"target_type": "task", "target_id": 5}},
    }
    issue = _intent_completeness_issue(
        intent, "unassign everyone from task 5 then delete it"
    )
    assert issue is not None


def test_intent_completeness_detects_field_update_dropping_another_action():
    """"change task 5 priority to high and assign it to @Alice" resolving
    to a bare work.update can only mean the assignment was dropped --
    work.update is a generic field tweak, never itself an assign/complete/
    start/unassign/delete, so any of those verbs appearing alongside it is
    a sign something else was requested and silently lost."""
    intent = {
        "capability": "work.update",
        "arguments": {"target": 5, "field": "priority", "value": "high"},
    }
    issue = _intent_completeness_issue(
        intent, "change task 5 priority to high and assign it to @Alice"
    )
    assert issue is not None
    assert "assign" in issue


def test_intent_completeness_ignores_a_bare_field_update():
    intent = {
        "capability": "work.update",
        "arguments": {"target": 5, "field": "priority", "value": "high"},
    }
    assert _intent_completeness_issue(intent, "set task 5 priority to high") is None


def test_intent_completeness_ignores_negated_second_action():
    intent = {"capability": "work.start", "arguments": {"target": 5}}
    assert _intent_completeness_issue(
        intent, "don't complete task 5, just start it"
    ) is None


def test_intent_completeness_treats_delete_task_and_delete_event_as_one_family():
    """delete_task and delete_event share the same delete/cancel vocabulary
    and must count as ONE action family, not two -- otherwise a bare
    "delete task 5" would always look like it's missing a second action
    against itself."""
    assert _intent_completeness_issue(
        {"capability": "work.delete_task", "arguments": {"target": 5}}, "delete task 5"
    ) is None
    assert _intent_completeness_issue(
        {"capability": "work.delete_event", "arguments": {"target": 5}}, "cancel event 5"
    ) is None


def test_intent_completeness_ignores_remove_person_from_task_as_delete():
    """"remove" is unassign vocabulary when the object is a person, not the
    task itself -- it must not collide with the delete family."""
    intent = {"capability": "work.unassign", "arguments": {"target": 5}}
    assert _intent_completeness_issue(intent, "remove @Alice from task 5") is None


def test_fallback_selects_closest_existing_command():
    assert fallback_command("please create an LFX event") == "!work create event"
    assert fallback_command("make a task for the docs") == "!work create task"
    assert fallback_command("show me progress") == "!reports"


def test_knowledge_context_supplies_high_confidence_program_defaults():
    context = build_knowledge_context({}, "create an LFX event for this year")
    assert "type=participation, category=lfx" in context
    assert "Current date:" in context


def test_named_entity_resolution_corrects_typos_and_maps_update_intent():
    candidate = {"type": "event", "id": 42, "name": "Spring Term 2026", "score": 0.9}
    with patch("features.natural_language._named_entity_candidates", return_value=[candidate]):
        command = resolve_named_entity_command(
            "!help", "show changes on the Sprng Term event", object()
        )
    assert command == "!work history event 42"


def test_named_collection_membership_is_compiled_from_live_entity_context():
    candidate = {"name": "lfx-applicants", "score": 0.9}
    with patch("features.natural_language._named_collection_candidates", return_value=[candidate]):
        command = resolve_named_collection_command(
            "!help",
            "add @~Shaurya to lfx-applicants",
            object(),
            ["50990036295744@lid"],
        )
    assert command == "!labels add lfx-applicants"


def test_model_output_cannot_escape_the_command_allowlist():
    assert validate_command("!work update event 4 prs 3") == "!work update event 4 prs 3"
    assert validate_command("delete everything") is None
    assert validate_command("!update-event 4 | name old") == "!update-event 4 | name old"
    assert validate_command("!reminders") == "!reminders"
    assert validate_command("!work\n!remove 1") is None
    assert validate_command("```!my```") is None


def test_bot_mention_translates_and_reenters_dispatch_with_sender_context():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@Bot please show my workload")
    dispatch = MagicMock()

    with patch("features.natural_language._get_mentioned_jids", return_value=[
        "bot@s.whatsapp.net",
        "target@s.whatsapp.net",
    ]), patch.object(
        MistralCommandTranslator,
        "translate",
        return_value=("!my", ""),
    ):
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    translated = dispatch.call_args.args[0]
    assert translated.Info is message.Info
    assert translated.Info.MessageSource.Sender == "member@s.whatsapp.net"
    assert translated.Message.conversation == "!my"
    assert translated._pbbot_nl_command is True


def test_natural_language_trigger_is_available_in_direct_chat():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message(
        "show my workload",
        "member@s.whatsapp.net",
        server="s.whatsapp.net",
    )
    dispatch = MagicMock()

    with patch.object(
        MistralCommandTranslator,
        "translate",
        return_value=("!my", ""),
    ):
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    dispatch.assert_called_once()


def test_me_alias_triggers_translation_and_resolves_to_sender_mention():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message("@me assign task 7 to me")
    dispatch = MagicMock()

    with patch("features.natural_language._get_mentioned_jids", return_value=[]), patch.object(
        MistralCommandTranslator,
        "translate",
        return_value=("!work assign task 7 | @me", ""),
    ):
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    dispatch.assert_not_called()
    assert "safely resolve" in str(client.send_message.call_args)


def test_natural_language_card_design_reenters_dispatch_with_design_spec():
    client = MagicMock()
    client.get_me.return_value = SimpleNamespace(
        JID="bot@s.whatsapp.net", LID="999999@lid"
    )
    message = make_message(
        "@bot create a sarcastic congratulations card for Zodiak for PBCTF 5.0"
    )
    dispatch = MagicMock()
    intent = {
        "capability": "card.design",
        "arguments": {
            "base_template": "hackathon",
            "name": "Zodiak",
            "text": "For PBCTF 5.0",
            "accent": "#A855F7",
        },
    }

    with patch("features.natural_language._get_mentioned_jids", return_value=["bot@s.whatsapp.net"]), \
         patch.object(MistralCommandTranslator, "translate", return_value=(intent, "")), \
         patch.object(MistralCardDesigner, "design", return_value=intent):
        handler = register(client, {"mistral_api_key": "secret"})
        assert handler(client, message, dispatch) is True

    translated = dispatch.call_args.args[0]
    assert translated.Message.conversation == "!card hackathon | Zodiak | For PBCTF 5.0"
    assert translated._pbbot_card_design["base_template"] == "hackathon"
