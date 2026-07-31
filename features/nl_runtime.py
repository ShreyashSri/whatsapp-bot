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
from typing import Callable

from db.auth import get_active_admin_jids, normalize_jid
from db.subgroup_store import SubgroupStore


@dataclass(frozen=True)
class CapabilityContract:
    """Execution requirements for one semantic capability."""

    target: str = "none"  # none, optional, or required


CAPABILITY_CONTRACTS: dict[str, CapabilityContract] = {
    # These handlers cannot perform the operation without concrete members.
    "collections.add": CapabilityContract("required"),
    "collections.remove": CapabilityContract("required"),
    "labels.remove": CapabilityContract("required"),
    "work.assign": CapabilityContract("required"),
    "work.unassign": CapabilityContract("required"),
    # A bare label add intentionally preserves legacy "add myself" semantics.
    "labels.add": CapabilityContract("optional"),
}

TARGET_RESOLVERS = frozenset(
    {
        "current_chat_members",
        "collection_members",
        "active_admins",
        "sender",
        "explicit_mentions",
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


def target_expression(arguments: dict) -> tuple[str, str]:
    """Return the canonical (resolver, value) target expression."""
    audience = arguments.get("audience")
    if isinstance(audience, dict):
        resolver = audience.get("resolver") or audience.get("kind") or ""
        value = audience.get("value") or audience.get("name") or ""
        return str(resolver), str(value)

    # Transitional compatibility for early structured responses. Do not
    # interpret a work item string in arguments["target"] as an audience.
    target = arguments.get("target")
    if isinstance(target, dict):
        resolver = target.get("resolver") or target.get("kind") or ""
        value = target.get("value") or target.get("name") or ""
        return str(resolver), str(value)

    resolver = arguments.get("target_scope") or ""
    value = arguments.get("target_collection") or ""
    return str(resolver), str(value)


def target_is_declared(arguments: dict, visible_mentions: list[str]) -> bool:
    resolver, _ = target_expression(arguments)
    if resolver == "explicit_mentions":
        return bool(visible_mentions)
    if resolver:
        return True
    return bool(arguments.get("mention_indices") and visible_mentions)


def target_is_required_and_missing(
    intent: dict,
    visible_mentions: list[str],
) -> bool:
    capability = str(intent.get("capability") or "")
    return (
        contract_for(capability).target == "required"
        and not target_is_declared(intent.get("arguments", {}), visible_mentions)
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
    resolve_collection: Callable[[object], str | None],
    visible_mentions: list[str] | None = None,
) -> TargetResolution:
    """Resolve a declared semantic audience to concrete JIDs."""
    arguments = intent.get("arguments", {})
    resolver, value = target_expression(arguments)

    if not resolver and arguments.get("mention_indices") and visible_mentions:
        resolver = "explicit_mentions"
    if not resolver:
        return TargetResolution()
    if resolver not in TARGET_RESOLVERS:
        return TargetResolution(error="The requested audience resolver is not available.")

    try:
        if resolver == "explicit_mentions":
            members = visible_mentions or []
        elif resolver == "sender":
            members = [message.Info.MessageSource.Sender]
        elif resolver == "active_admins":
            if factory is None:
                return TargetResolution(error="Active administrators are unavailable.")
            members = get_active_admin_jids(factory)
        elif resolver == "collection_members":
            if factory is None or not value:
                return TargetResolution(error="The referenced member collection is missing.")
            resolved_name = resolve_collection(value)
            if not resolved_name:
                return TargetResolution(error="The referenced member collection was not found.")
            members = SubgroupStore(factory).read().get(resolved_name, [])
        elif resolver == "current_chat_members":
            chat = message.Info.MessageSource.Chat
            if getattr(chat, "Server", "") != "g.us":
                return TargetResolution(error="Current group members are unavailable here.")
            from features.community_tag import get_group_member_jids

            members = get_group_member_jids(client, chat)
        else:
            members = []
    except Exception:
        return TargetResolution(error="I couldn't resolve that audience from runtime data.")

    deduped = _dedupe_members(members, self_jids)
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
