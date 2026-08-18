"""Semantic intent contracts and deterministic runtime target resolution.

The language model may describe an operation and an audience expression, but
it never supplies executable JIDs. This module is the boundary between that
semantic plan and the legacy command handlers:

    intent -> contract validation -> target resolution -> ready-to-compile

Keeping the contract here prevents individual compiler branches from silently
accepting incomplete intents.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from db.auth import get_active_admin_jids, normalize_jid
from db.subgroup_store import SubgroupStore


def _positive_action_present(text: str, verbs: tuple[str, ...]) -> bool:
    """Match an action verb unless the request explicitly negates it."""
    lowered = str(text or "").casefold()
    for verb in verbs:
        for match in re.finditer(rf"\b{re.escape(verb)}\b", lowered):
            prefix = lowered[max(0, match.start() - 40):match.start()]
            if re.search(
                r"(?:\bdo\s+not\b|\bdon't\b|\bdont\b|\bnever\b|\bnot\b|\bwithout\b)"
                r"(?:\s+[\w'-]+){0,3}\s*$",
                prefix,
            ):
                continue
            return True
    return False


@dataclass(frozen=True)
class CapabilityContract:
    """Execution requirements for one semantic capability."""

    target: str = "none"  # none, optional, or required
    entity_scope: bool = False  # operation should honor a named entity scope
    repeatable: bool = True  # may appear multiple times in one plan
    produces: frozenset[str] = frozenset()  # durable fields exposed to later steps


CAPABILITY_CONTRACTS: dict[str, CapabilityContract] = {
    # These handlers cannot perform the operation without concrete members.
    "collections.add": CapabilityContract("required"),
    "collections.remove": CapabilityContract("required"),
    "collections.delete": CapabilityContract("optional"),
    "collections.list": CapabilityContract("optional"),
    "collections.info": CapabilityContract("optional"),
    "collections.tag": CapabilityContract("required"),
    "labels.remove": CapabilityContract("required"),
    "admin.add_user": CapabilityContract("required"),
    "admin.remove_user": CapabilityContract("required"),
    "work.assign": CapabilityContract("required"),
    # For unassign, audience is optional: omitting it means "remove all current assignees".
    "work.unassign": CapabilityContract("optional"),
    "whatsapp.user_info": CapabilityContract("required"),
    "whatsapp.add_group_members": CapabilityContract("required"),
    "whatsapp.remove_group_members": CapabilityContract("required"),
    "whatsapp.profile_pictures": CapabilityContract("required"),
    "whatsapp.block_contacts": CapabilityContract("required"),
    "whatsapp.unblock_contacts": CapabilityContract("required"),
    "whatsapp.create_group": CapabilityContract("optional", produces=frozenset({"group_jid", "name", "member_jids"})),
    # A bare label add intentionally preserves legacy "add myself" semantics.
    "labels.add": CapabilityContract("optional"),
    # Scoped reads must not silently degrade to a global overview when the
    # user's wording names an event, task, label, or other stored entity.
    "work.overview": CapabilityContract(entity_scope=True),
    "work.history": CapabilityContract(entity_scope=True),
    "work.status": CapabilityContract(entity_scope=True),
    "work.start": CapabilityContract(entity_scope=True),
    "work.complete": CapabilityContract(entity_scope=True),
    "work.update": CapabilityContract(entity_scope=True),
    "work.list_event_tasks": CapabilityContract(entity_scope=True),
    "reports.progress": CapabilityContract(entity_scope=True),
    "schema.show": CapabilityContract(entity_scope=True),
    "work.create_event": CapabilityContract(produces=frozenset({"event_id"})),
    "work.create_task": CapabilityContract(produces=frozenset({"task_id"})),
    "reminders.send": CapabilityContract(entity_scope=True),
}

TARGET_RESOLVERS = frozenset(
    {
        "current_chat_members",
        "collection_members",
        "active_admins",
        "sender",
        "explicit_mentions",
        "plan_output",
    }
)


@dataclass(frozen=True)
class TargetResolution:
    members: tuple[str, ...] = ()
    resolver: str = ""
    error: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.members) and not self.error


def contract_for(capability: str) -> CapabilityContract:
    return CAPABILITY_CONTRACTS.get(capability, CapabilityContract())


def verify_operation_result(intent: dict, result: object) -> str | None:
    """Check deterministic postconditions before exposing a tool result."""
    capability = str(intent.get("capability") or "")
    if not isinstance(result, dict):
        from features.agent_runtime import tool_spec

        if tool_spec(capability).produces:
            return "the operation returned no structured result"
        return None
    if result.get("ok") is False:
        return str(result.get("error") or "the operation could not be completed")
    from features.agent_runtime import tool_spec
    missing_outputs = [
        field for field in tool_spec(capability).produces
        if field not in result
    ]
    if missing_outputs:
        return "the operation omitted declared outputs: " + ", ".join(sorted(missing_outputs))
    if capability not in {"work.create_event", "work.create_task"}:
        return None
    arguments = intent.get("arguments", {})
    if capability == "work.create_event" and not result.get("event_id"):
        return "event creation returned no event ID"
    if capability == "work.create_task":
        if not result.get("task_id"):
            return "task creation returned no task ID"
        expected_event = arguments.get("event_id")
        if (
            expected_event is not None
            and str(result.get("event_id")) != str(expected_event)
        ):
            return "task was not linked to the requested event"
    return None


def validate_mutation_policy(intent: dict, text: str, members: list[str] | None = None) -> str | None:
    """Fail closed when a destructive intent has no bounded, explicit scope."""
    capability = str(intent.get("capability") or "")
    arguments = intent.get("arguments", {})
    destructive = {
        "admin.remove_user", "collections.remove", "collections.delete",
        "labels.remove", "labels.delete", "work.unassign", "work.delete_event",
        "work.delete_task", "schema.delete", "whatsapp.remove_group_members",
        "whatsapp.leave_group", "whatsapp.revoke_message", "whatsapp.block_contacts",
        "whatsapp.unlink_group", "whatsapp.group_invite", "whatsapp.contact_qr",
        "whatsapp.set_profile_photo",
    }
    # Retrieving an invite or QR is read-only; only the optional revoke form is
    # destructive. A model must not be able to smuggle ``revoke=true`` into a
    # harmless retrieval request, or silently downgrade an explicit revoke to
    # a read.
    if capability in {"whatsapp.group_invite", "whatsapp.contact_qr"}:
        revoke = arguments.get("revoke")
        is_revoke = revoke is True or (
            isinstance(revoke, str)
            and revoke.strip().casefold() in {"true", "yes", "y", "1", "revoke"}
        )
        lowered = str(text or "").casefold()
        revoke_words = (
            ("revoke", "invalidate", "reset")
            if capability == "whatsapp.group_invite"
            else ("revoke", "invalidate", "reset", "delete")
        )
        explicit_revoke = _positive_action_present(lowered, revoke_words)
        if explicit_revoke and not is_revoke:
            return f"{capability} requires argument revoke"
        if is_revoke and not explicit_revoke:
            return f"{capability} requires explicit destructive wording"
        if not is_revoke:
            return None
    if capability not in destructive:
        return None
    lowered = str(text or "").casefold()
    verbs = {
        "admin.remove_user": ("remove", "deactivate", "delete"),
        "collections.remove": ("remove", "delete", "exclude", "leave"),
        "collections.delete": ("delete", "remove"),
        "labels.remove": ("remove", "delete", "exclude", "leave"),
        "labels.delete": ("delete", "remove"),
        "work.unassign": ("unassign", "remove", "clear"),
        "work.delete_event": ("delete", "remove"),
        "work.delete_task": ("delete", "remove"),
        "schema.delete": ("delete", "remove", "clear"),
        "whatsapp.remove_group_members": ("remove", "kick"),
        "whatsapp.leave_group": ("leave",),
        "whatsapp.revoke_message": ("revoke", "delete"),
        "whatsapp.block_contacts": ("block",),
        "whatsapp.unlink_group": ("unlink",),
        "whatsapp.group_invite": ("revoke", "invalidate", "reset"),
        "whatsapp.contact_qr": ("revoke", "invalidate", "reset", "delete"),
        "whatsapp.set_profile_photo": ("set", "change", "update", "replace", "upload", "remove", "clear"),
    }
    if not _positive_action_present(lowered, verbs[capability]):
        return f"{capability} requires explicit destructive wording"
    if capability == "collections.delete" and not str(arguments.get("collection") or "").strip():
        bulk_delete = re.search(
            r"\b(?:all|every|everything)\b.*\b(?:subgroups?|collections?)\b|"
            r"\b(?:subgroups?|collections?)\b.*\b(?:all|every|everything)\b",
            lowered,
        )
        if not bulk_delete:
            return "collections.delete requires argument collection"
    if capability == "admin.remove_user" and not members:
        return "admin.remove_user requires argument mention_indices"
    if capability == "labels.delete" and not str(arguments.get("collection") or "").strip():
        return "labels.delete requires argument collection"
    if capability == "work.assign":
        has_target = any(
            arguments.get(key) is not None
            for key in ("target", "target_id", "target_name", "event_id", "task_id")
        )
        if not has_target:
            return "work.assign requires argument target"
    if capability == "work.unassign":
        has_target = any(
            arguments.get(key) is not None
            for key in ("target", "target_id", "target_name", "event_id", "task_id")
        )
        # The direct adapter supports an explicit "unassign everything"
        # operation. It is safe only when the user actually used bulk wording;
        # a missing target in any other request remains an exact validation
        # error.
        if not has_target and not re.search(
            r"\b(?:all|everything|every|everyone)\b|\bclear\s+(?:all\s+)?assignments?\b",
            lowered,
        ):
            return "work.unassign requires argument target"
        audience_declared = any(
            arguments.get(key) is not None
            for key in ("audience", "mention_indices")
        )
        if audience_declared and not members:
            return "work.unassign requires argument audience"
    if capability in {"collections.remove", "labels.remove", "whatsapp.remove_group_members"} and not members:
        return f"{capability} requires argument audience"
    return None


def _normalize_collection_value(value: object) -> object:
    """Flatten a single-element list to a plain string.

    The model occasionally wraps a collection name in a list (e.g.
    ``['abc']``).  Downstream resolvers require a plain string, so unwrap
    here when the list contains exactly one item.
    """
    if isinstance(value, list):
        # Strip any leading @ before comparing
        strings = [v.strip().lstrip("@") if isinstance(v, str) else v for v in value if v]
        if len(strings) == 1 and isinstance(strings[0], str):
            return strings[0]
    if isinstance(value, str):
        return value.strip().lstrip("@") if value.strip().startswith("@") else value
    return value


def target_expression(arguments: dict) -> tuple[str, object]:
    """Return the canonical (resolver, value) target expression.

    ``arguments["audience"]`` is the only shape a request's "who" is ever
    read from. ``arguments["target"]`` is reserved exclusively for work-item
    identity (task/event) -- see validate_intent's typed work-target
    handling -- and is never read as an audience source here, even if it
    happens to contain a resolver-shaped key. There is intentionally no
    fallback to any other shape: intents are generated fresh per message
    and never stored or replayed, nothing in the codebase constructs an
    alternate shape, and the prompt does not instruct the model to either --
    keeping one "for compatibility" would only leave a second, untested way
    to say the same thing.
    """
    audience = arguments.get("audience")
    if not isinstance(audience, dict):
        return "", ""
    resolver = audience.get("resolver") or audience.get("kind") or ""
    if not resolver:
        return "", ""
    value = audience.get("value") or audience.get("name") or audience.get("collection") or ""
    return str(resolver), _normalize_collection_value(value)


def mention_indices_expression(arguments: dict) -> object:
    """Return ``mention_indices`` from wherever the model placed it.

    ``mention_indices`` only makes sense paired with an ``explicit_mentions``
    audience, so its natural position is nested inside ``arguments["audience"]``
    -- and real model responses do nest it there. Earlier plans placed it as
    a sibling of ``audience`` at the top level instead. Checking only one of
    the two locations silently drops the model's requested subset and falls
    back to "every visible mention" whenever it chose the other -- this is
    the single place that decides between them, so every caller sees the
    same answer regardless of which shape the model produced.
    """
    audience = arguments.get("audience")
    if isinstance(audience, dict) and audience.get("mention_indices") is not None:
        return audience.get("mention_indices")
    return arguments.get("mention_indices")


def target_is_declared(arguments: dict, visible_mentions: list[str]) -> bool:
    resolver, _ = target_expression(arguments)
    if resolver == "explicit_mentions":
        return bool(visible_mentions)
    if resolver:
        return True
    return bool(mention_indices_expression(arguments) and visible_mentions)


def target_is_required_and_missing(
    intent: dict,
    visible_mentions: list[str],
) -> bool:
    capability = str(intent.get("capability") or "")
    return (
        contract_for(capability).target == "required"
        and not target_is_declared(intent.get("arguments", {}), visible_mentions)
    )


def entity_scope_is_missing(intent: dict, entity_candidates: list[dict]) -> bool:
    """Detect a scoped operation that discarded a named runtime entity."""
    if not entity_candidates:
        return False
    contract = contract_for(str(intent.get("capability") or ""))
    if not contract.entity_scope:
        return False
    arguments = intent.get("arguments", {})
    return not any(
        arguments.get(key)
        for key in ("target_id", "target_name", "event_id", "task_id", "collection")
    )


def _dedupe_members(members, self_jids: set[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for member in members:
        jid = normalize_jid(member)
        if not jid or jid in self_jids or jid in seen:
            continue
        seen.add(jid)
        result.append(jid)
    return tuple(result)


def resolve_target(
    client,
    message,
    intent: dict,
    self_jids: set[str],
    factory,
    resolve_collection: Callable[[object], str | list[str] | None],
    visible_mentions: list[str] | None = None,
) -> TargetResolution:
    """Resolve a declared semantic audience to concrete JIDs."""
    arguments = intent.get("arguments", {})
    resolver, value = target_expression(arguments)
    mention_indices = mention_indices_expression(arguments)

    if not resolver and mention_indices and visible_mentions:
        resolver = "explicit_mentions"
    if not resolver:
        return TargetResolution()
    if resolver not in TARGET_RESOLVERS:
        return TargetResolution(error="The requested audience resolver is not available.")

    try:
        if resolver == "explicit_mentions":
            if mention_indices is None:
                members = visible_mentions or []
            else:
                try:
                    members = [visible_mentions[index] for index in mention_indices]
                except (IndexError, TypeError):
                    return TargetResolution(
                        error="One or more requested mentions are unavailable."
                    )
        elif resolver == "sender":
            members = [message.Info.MessageSource.Sender]
        elif resolver == "active_admins":
            if factory is None:
                return TargetResolution(error="Active administrators are unavailable.")
            members = get_active_admin_jids(factory)
        elif resolver == "collection_members":
            if factory is None or not value:
                return TargetResolution(error="The referenced member collection is missing.")
            # Keep a list as a list. Joining two collection names into one
            # fuzzy query can select an unrelated subgroup or fail with an
            # opaque error; the resolver callback can resolve each name and
            # fail closed if any one is ambiguous.
            lookup_value: object = value
            resolved_names = resolve_collection(lookup_value)
            if isinstance(resolved_names, str):
                resolved_names = [resolved_names]
            if not resolved_names:
                label = ", ".join(str(item) for item in lookup_value) if isinstance(lookup_value, list) else str(lookup_value)
                return TargetResolution(error=f"Subgroup '{label}' not found or is ambiguous. Use `!list-subgroups` to see available subgroups.")
            collections = SubgroupStore(factory).read()
            members = [
                member
                for name in resolved_names
                for member in collections.get(name, [])
            ]
            # Stored collections may predate the self-message guard. Filter the
            # paired account here as well so it cannot become an audience again.
            deduped = _dedupe_members(members, self_jids)
            if not deduped:
                return TargetResolution(
                    resolver=resolver,
                    error=f"Subgroup '{resolved_names[0]}' exists but has no members yet.",
                )
            return TargetResolution(members=deduped, resolver=resolver)
        elif resolver == "current_chat_members":
            chat = message.Info.MessageSource.Chat
            if getattr(chat, "Server", "") != "g.us":
                return TargetResolution(error="Current group members are unavailable here.")
            from features.community_tag import get_group_member_jids

            members = get_group_member_jids(client, chat)
        elif resolver == "plan_output":
            if not isinstance(value, list):
                return TargetResolution(error="The referenced plan output is not a member list.")
            members = value
        else:
            members = []
    except Exception:
        return TargetResolution(error="I couldn't resolve that audience from runtime data.")

    # A bot can be an explicit assignee, but it must never re-enter broad
    # audience resolvers such as current_chat_members or collection_members.
    active_self_jids = (
        set()
        if intent.get("capability") in {"work.assign", "work.unassign"}
        and resolver == "explicit_mentions"
        else self_jids
    )
    deduped = _dedupe_members(members, active_self_jids)
    if not deduped:
        return TargetResolution(
            resolver=resolver,
            error="The audience resolved to no eligible members.",
        )
    return TargetResolution(members=deduped, resolver=resolver)


def validate_execution_ready(
    intent: dict,
    resolution: TargetResolution,
    visible_mentions: list[str],
) -> str | None:
    """Return a user-safe error when a semantic step cannot execute."""
    capability = str(intent.get("capability") or "")
    contract = contract_for(capability)
    if contract.target == "required":
        if not target_is_declared(intent.get("arguments", {}), visible_mentions):
            return "I couldn't identify who this operation should affect."
        if not resolution.ready:
            return resolution.error or "I couldn't resolve the requested audience."
    if resolution.error and target_is_declared(intent.get("arguments", {}), visible_mentions):
        return resolution.error
    return None
