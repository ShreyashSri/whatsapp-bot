"""Agent-runtime contracts for planning, execution, and observation.

This layer intentionally stays independent of WhatsApp and database models.
Domain functions remain the tools; this module describes how a plan may use
them and validates the plan before any side effect is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import time
from typing import Any


_REFERENCE_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9_-]*)\.[A-Za-z][A-Za-z0-9_.-]*")
MAX_PLAN_STEPS = 16


@dataclass(frozen=True)
class ToolSpec:
    capability: str
    repeatable: bool = True
    mutating: bool = False
    produces: frozenset[str] = field(default_factory=frozenset)
    arguments: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    description: str = ""
    executor: str = "command"
    permission: str = "member"
    destructive: bool = False


_CAPABILITY_NAMES = (
    "help.show", "admin.add_user", "admin.remove_user", "admin.list_users", "admin.list_admins",
    "labels.list", "labels.of", "labels.add", "labels.remove", "labels.delete",
    "collections.add", "collections.remove", "collections.delete", "collections.list", "collections.info",
    "work.my", "work.undo", "work.overview", "work.history", "work.status", "work.start", "work.complete", "work.update", "work.edit",
    "work.set_lifecycle", "work.update_event", "work.delete_event", "work.delete_task",
    "work.assign", "work.unassign", "work.create_event", "work.create_task", "work.list_event_tasks",
    "reports.summary", "reports.progress", "reports.status", "audit.list",
    "media.add", "media.remove", "media.todo", "media.posted", "media.unposted", "media.posted_list",
    "card.create", "card.create_pdf", "card.design", "card.design_pdf",
    "schema.show", "schema.set", "schema.add", "schema.delete",
    "reminders.status", "reminders.config", "reminders.run", "reminders.history",
    "whatsapp.send", "whatsapp.reply", "whatsapp.react",
    "whatsapp.group_info", "whatsapp.group_members", "whatsapp.user_info",
    "whatsapp.send_attachment",
    "whatsapp.add_group_members", "whatsapp.remove_group_members",
    "whatsapp.rename_group", "whatsapp.group_invite",
    "whatsapp.set_group_announce", "whatsapp.set_group_locked",
    "whatsapp.set_group_topic", "whatsapp.set_disappearing_timer",
    "whatsapp.send_contact", "whatsapp.send_poll",
    "whatsapp.joined_groups", "whatsapp.community_subgroups",
    "whatsapp.profile_pictures", "whatsapp.group_join_requests",
    "whatsapp.linked_group_members",
    "whatsapp.create_group", "whatsapp.join_group", "whatsapp.leave_group",
    "whatsapp.is_on_whatsapp", "whatsapp.block_contacts",
    "whatsapp.unblock_contacts", "whatsapp.pin_message",
    "whatsapp.revoke_message",
    "whatsapp.set_group_photo", "whatsapp.contact_devices", "whatsapp.blocklist",
    "whatsapp.resolve_contact", "whatsapp.group_info_from_link",
    "whatsapp.link_group", "whatsapp.unlink_group",
    "whatsapp.contact_qr", "whatsapp.set_profile_name",
    "whatsapp.set_status", "whatsapp.set_profile_photo",
    "whatsapp.account_info",
)

TOOL_SPECS: dict[str, ToolSpec] = {
    capability: ToolSpec(capability) for capability in _CAPABILITY_NAMES
}
TOOL_SPECS.update({
    "work.my": ToolSpec(
        "work.my", produces=frozenset({"rows", "row_count"}), executor="direct"
    ),
    "work.undo": ToolSpec("work.undo", mutating=True),
    "work.overview": ToolSpec(
        "work.overview", produces=frozenset({"rows", "row_count"}), executor="direct"
    ),
    "work.list_event_tasks": ToolSpec(
        "work.list_event_tasks", produces=frozenset({"tasks", "task_count"}), executor="direct"
    ),
    "work.create_event": ToolSpec(
        "work.create_event", mutating=True, produces=frozenset({"event_id"}), executor="direct"
    ),
    "work.create_task": ToolSpec(
        "work.create_task", mutating=True, produces=frozenset({"task_id", "event_id"}), executor="direct"
    ),
    "work.assign": ToolSpec("work.assign", mutating=True, executor="direct"),
    "work.unassign": ToolSpec("work.unassign", mutating=True, executor="direct"),
    "work.set_lifecycle": ToolSpec("work.set_lifecycle", mutating=True),
    "work.update_event": ToolSpec("work.update_event", mutating=True),
    "work.delete_event": ToolSpec("work.delete_event", mutating=True),
    "work.delete_task": ToolSpec("work.delete_task", mutating=True),
    "collections.add": ToolSpec("collections.add", mutating=True, executor="direct"),
    "collections.remove": ToolSpec("collections.remove", mutating=True, executor="direct"),
    "collections.delete": ToolSpec("collections.delete", mutating=True, executor="direct"),
    "collections.list": ToolSpec("collections.list", produces=frozenset({"subgroups", "collection_count"}), executor="direct"),
    "collections.info": ToolSpec("collections.info", produces=frozenset({"members", "member_count"}), executor="direct"),
    "labels.add": ToolSpec("labels.add", mutating=True, executor="direct"),
    "labels.remove": ToolSpec("labels.remove", mutating=True, executor="direct"),
    "labels.delete": ToolSpec("labels.delete", mutating=True),
    "admin.add_user": ToolSpec("admin.add_user", mutating=True),
    "admin.remove_user": ToolSpec("admin.remove_user", mutating=True),
    "schema.set": ToolSpec("schema.set", mutating=True),
    "schema.add": ToolSpec("schema.add", mutating=True),
    "schema.delete": ToolSpec("schema.delete", mutating=True),
    "reminders.config": ToolSpec("reminders.config", mutating=True),
    "reminders.run": ToolSpec("reminders.run", mutating=True),
    "whatsapp.send": ToolSpec("whatsapp.send", mutating=True, executor="direct"),
    "whatsapp.reply": ToolSpec("whatsapp.reply", mutating=True, executor="direct"),
    "whatsapp.react": ToolSpec("whatsapp.react", mutating=True, executor="direct"),
    "whatsapp.group_info": ToolSpec(
        "whatsapp.group_info", produces=frozenset({"group_jid", "member_jids", "member_count"}), executor="direct"
    ),
    "whatsapp.group_members": ToolSpec(
        "whatsapp.group_members", produces=frozenset({"group_jid", "member_jids", "member_count"}), executor="direct"
    ),
    "whatsapp.user_info": ToolSpec(
        "whatsapp.user_info", produces=frozenset({"users", "user_count"}), executor="direct"
    ),
    "whatsapp.send_attachment": ToolSpec(
        "whatsapp.send_attachment", mutating=True, executor="direct"
    ),
    "whatsapp.add_group_members": ToolSpec(
        "whatsapp.add_group_members", mutating=True, executor="direct"
    ),
    "whatsapp.remove_group_members": ToolSpec(
        "whatsapp.remove_group_members", mutating=True, executor="direct"
    ),
    "whatsapp.rename_group": ToolSpec(
        "whatsapp.rename_group", mutating=True, executor="direct"
    ),
    "whatsapp.group_invite": ToolSpec(
        "whatsapp.group_invite", executor="direct"
    ),
    "whatsapp.joined_groups": ToolSpec(
        "whatsapp.joined_groups", produces=frozenset({"groups", "group_count"}), executor="direct"
    ),
    "whatsapp.community_subgroups": ToolSpec(
        "whatsapp.community_subgroups", produces=frozenset({"groups", "group_count"}), executor="direct"
    ),
    "whatsapp.set_group_announce": ToolSpec("whatsapp.set_group_announce", mutating=True, executor="direct"),
    "whatsapp.set_group_locked": ToolSpec("whatsapp.set_group_locked", mutating=True, executor="direct"),
    "whatsapp.set_group_topic": ToolSpec("whatsapp.set_group_topic", mutating=True, executor="direct"),
    "whatsapp.set_disappearing_timer": ToolSpec("whatsapp.set_disappearing_timer", mutating=True, executor="direct"),
    "whatsapp.send_contact": ToolSpec("whatsapp.send_contact", mutating=True, executor="direct"),
    "whatsapp.send_poll": ToolSpec("whatsapp.send_poll", mutating=True, executor="direct"),
    "whatsapp.profile_pictures": ToolSpec(
        "whatsapp.profile_pictures", produces=frozenset({"profiles", "profile_count"}), executor="direct"
    ),
    "whatsapp.group_join_requests": ToolSpec(
        "whatsapp.group_join_requests", produces=frozenset({"requests", "request_count"}), executor="direct"
    ),
    "whatsapp.linked_group_members": ToolSpec(
        "whatsapp.linked_group_members", produces=frozenset({"members", "member_count"}), executor="direct"
    ),
    "whatsapp.create_group": ToolSpec(
        "whatsapp.create_group", mutating=True,
        produces=frozenset({"group_jid", "name", "member_jids"}), executor="direct"
    ),
    "whatsapp.join_group": ToolSpec(
        "whatsapp.join_group", mutating=True,
        produces=frozenset({"group_jid"}), executor="direct"
    ),
    "whatsapp.leave_group": ToolSpec(
        "whatsapp.leave_group", mutating=True, executor="direct"
    ),
    "whatsapp.is_on_whatsapp": ToolSpec(
        "whatsapp.is_on_whatsapp", produces=frozenset({"numbers", "number_count"}), executor="direct"
    ),
    "whatsapp.block_contacts": ToolSpec(
        "whatsapp.block_contacts", mutating=True, executor="direct"
    ),
    "whatsapp.unblock_contacts": ToolSpec(
        "whatsapp.unblock_contacts", mutating=True, executor="direct"
    ),
    "whatsapp.pin_message": ToolSpec(
        "whatsapp.pin_message", mutating=True, executor="direct"
    ),
    "whatsapp.revoke_message": ToolSpec(
        "whatsapp.revoke_message", mutating=True, executor="direct"
    ),
    "whatsapp.set_group_photo": ToolSpec(
        "whatsapp.set_group_photo", mutating=True, executor="direct"
    ),
    "whatsapp.contact_devices": ToolSpec(
        "whatsapp.contact_devices", produces=frozenset({"devices", "device_count"}), executor="direct"
    ),
    "whatsapp.blocklist": ToolSpec(
        "whatsapp.blocklist", produces=frozenset({"contacts", "contact_count"}), executor="direct"
    ),
    "whatsapp.resolve_contact": ToolSpec(
        "whatsapp.resolve_contact", produces=frozenset({"phone_jid", "lid_jid"}), executor="direct"
    ),
    "whatsapp.group_info_from_link": ToolSpec(
        "whatsapp.group_info_from_link", produces=frozenset({"group_jid", "name", "member_count"}), executor="direct"
    ),
    "whatsapp.link_group": ToolSpec(
        "whatsapp.link_group", mutating=True, executor="direct"
    ),
    "whatsapp.unlink_group": ToolSpec(
        "whatsapp.unlink_group", mutating=True, executor="direct"
    ),
    "whatsapp.contact_qr": ToolSpec(
        "whatsapp.contact_qr", produces=frozenset({"link", "revoked"}), executor="direct"
    ),
    "whatsapp.set_profile_name": ToolSpec(
        "whatsapp.set_profile_name", mutating=True, executor="direct"
    ),
    "whatsapp.set_status": ToolSpec(
        "whatsapp.set_status", mutating=True, executor="direct"
    ),
    "whatsapp.set_profile_photo": ToolSpec(
        "whatsapp.set_profile_photo", mutating=True, executor="direct"
    ),
    "whatsapp.account_info": ToolSpec(
        "whatsapp.account_info", produces=frozenset({"jid", "lid", "name", "platform"}), executor="direct"
    ),
})

_TOOL_ARGUMENTS = {
    "help.show": ("module?",),
    "admin.add_user": ("role?", "mention_indices[]"),
    "admin.remove_user": ("mention_indices[]",),
    "labels.of": ("mention?",),
    "labels.add": ("collection", "audience?"),
    "labels.remove": ("collection", "audience?"),
    "labels.delete": ("collection",),
    "collections.add": ("collection", "audience?"),
    "collections.remove": ("collection", "audience?"),
    "collections.delete": ("collection?",),
    "collections.list": (),
    "collections.info": ("collection",),
    "work.my": ("status?",),
    "work.undo": (),
    "work.overview": ("status?", "target?"),
    "work.history": ("target",),
    "work.status": ("target",),
    "work.start": ("target",),
    "work.complete": ("target",),
    "work.update": ("target", "field", "value"),
    "work.edit": ("revision_id", "value"),
    "work.set_lifecycle": ("target", "status"),
    "work.update_event": ("target", "fields"),
    "work.delete_event": ("target",),
    "work.delete_task": ("target",),
    "work.assign": ("target", "audience?", "collections?"),
    "work.unassign": ("target", "audience?", "collections?"),
    "work.create_event": ("type", "category", "name", "description?", "start?", "end?", "labels?"),
    "work.create_task": ("title", "description?", "due?", "priority?", "event_id?"),
    "work.list_event_tasks": ("event", "status?"),
    "reports.progress": ("target",),
    "reports.status": ("status",),
    "audit.list": ("operation?",),
    "media.add": ("text",),
    "media.remove": ("id",),
    "media.posted": ("id", "stage"),
    "media.unposted": ("id", "stage"),
    "card.create": ("type", "name", "text", "event_name?", "logo_urls?"),
    "card.create_pdf": ("type", "name", "text", "event_name?", "logo_urls?"),
    "card.design": ("base_template?", "name", "occasion?", "tone?", "headline?", "body?", "accent?", "pill?", "logo_urls?"),
    "card.design_pdf": ("base_template?", "name", "occasion?", "tone?", "headline?", "body?", "accent?", "pill?", "logo_urls?"),
    "schema.show": ("target",),
    "schema.set": ("target", "fields"),
    "schema.add": ("target", "field"),
    "schema.delete": ("target", "field?"),
    "reminders.config": ("frequency?", "window?", "threshold?", "channel?"),
    "reminders.history": ("assignment_id?",),
    "whatsapp.send": ("text",),
    "whatsapp.reply": ("text",),
    "whatsapp.react": ("reaction",),
    "whatsapp.group_info": ("scope?",),
    "whatsapp.group_members": ("scope?",),
    "whatsapp.user_info": ("audience?",),
    "whatsapp.send_attachment": ("caption?", "filename?"),
    "whatsapp.add_group_members": ("audience",),
    "whatsapp.remove_group_members": ("audience",),
    "whatsapp.rename_group": ("name",),
    "whatsapp.group_invite": ("revoke?",),
    "whatsapp.joined_groups": (),
    "whatsapp.community_subgroups": (),
    "whatsapp.set_group_announce": ("enabled",),
    "whatsapp.set_group_locked": ("locked",),
    "whatsapp.set_group_topic": ("topic",),
    "whatsapp.set_disappearing_timer": ("seconds",),
    "whatsapp.send_contact": ("name", "number"),
    "whatsapp.send_poll": ("question", "options", "selectable_count?"),
    "whatsapp.profile_pictures": ("audience",),
    "whatsapp.group_join_requests": (),
    "whatsapp.linked_group_members": (),
    "whatsapp.create_group": ("name", "audience?"),
    "whatsapp.join_group": ("invite",),
    "whatsapp.leave_group": (),
    "whatsapp.is_on_whatsapp": ("numbers",),
    "whatsapp.block_contacts": ("audience",),
    "whatsapp.unblock_contacts": ("audience",),
    "whatsapp.pin_message": ("seconds",),
    "whatsapp.revoke_message": (),
    "whatsapp.set_group_photo": (),
    "whatsapp.contact_devices": ("audience",),
    "whatsapp.blocklist": (),
    "whatsapp.resolve_contact": ("identifier",),
    "whatsapp.group_info_from_link": ("link",),
    "whatsapp.link_group": ("parent_chat", "child_chat"),
    "whatsapp.unlink_group": ("parent_chat", "child_chat"),
    "whatsapp.contact_qr": ("revoke?",),
    "whatsapp.set_profile_name": ("name",),
    "whatsapp.set_status": ("status",),
    "whatsapp.set_profile_photo": (),
    "whatsapp.account_info": (),
}

# A plan may route a later WhatsApp step to a group produced by an earlier
# step. The value must be a runtime-resolved plan reference; the executor
# rejects raw/untrusted chat identifiers.
for _capability in tuple(_TOOL_ARGUMENTS):
    if _capability.startswith("whatsapp.") and "target_chat?" not in _TOOL_ARGUMENTS[_capability]:
        _TOOL_ARGUMENTS[_capability] = (*_TOOL_ARGUMENTS[_capability], "target_chat?")

_TOOL_REQUIRED = {
    "admin.add_user": ("mention_indices",),
    "admin.remove_user": ("mention_indices",),
    "labels.add": ("collection",),
    "labels.remove": ("collection",),
    "labels.delete": ("collection",),
    "collections.add": ("collection",),
    "collections.remove": ("collection",),
    "collections.info": ("collection",),
    "work.history": ("target",),
    "work.status": ("target",),
    "work.start": ("target",),
    "work.complete": ("target",),
    "work.update": ("target", "field", "value"),
    "work.edit": ("revision_id", "value"),
    "work.set_lifecycle": ("target", "status"),
    "work.update_event": ("target", "fields"),
    "work.delete_event": ("target",),
    "work.delete_task": ("target",),
    "work.assign": ("target",),
    "work.unassign": ("target",),
    "work.create_event": ("type", "category", "name"),
    "work.create_task": ("title",),
    "work.list_event_tasks": ("event",),
    "reports.progress": ("target",),
    "reports.status": ("status",),
    "media.add": ("text",),
    "media.remove": ("id",),
    "media.posted": ("id", "stage"),
    "media.unposted": ("id", "stage"),
    "card.create": ("type", "name", "text"),
    "card.create_pdf": ("type", "name", "text"),
    "card.design": ("name",),
    "card.design_pdf": ("name",),
    "schema.show": ("target",),
    "schema.set": ("target", "fields"),
    "schema.add": ("target", "field"),
    "schema.delete": ("target",),
    "whatsapp.send": ("text",),
    "whatsapp.reply": ("text",),
    "whatsapp.react": ("reaction",),
    "whatsapp.user_info": ("audience",),
    "whatsapp.add_group_members": ("audience",),
    "whatsapp.remove_group_members": ("audience",),
    "whatsapp.rename_group": ("name",),
    "whatsapp.send_contact": ("name", "number"),
    "whatsapp.send_poll": ("question", "options"),
    "whatsapp.profile_pictures": ("audience",),
    "whatsapp.block_contacts": ("audience",),
    "whatsapp.unblock_contacts": ("audience",),
    "whatsapp.is_on_whatsapp": ("numbers",),
    "whatsapp.contact_devices": ("audience",),
    "whatsapp.set_group_announce": ("enabled",),
    "whatsapp.set_group_locked": ("locked",),
    "whatsapp.set_group_topic": ("topic",),
    "whatsapp.set_disappearing_timer": ("seconds",),
    "whatsapp.pin_message": ("seconds",),
    "whatsapp.resolve_contact": ("identifier",),
    "whatsapp.group_info_from_link": ("link",),
    "whatsapp.create_group": ("name",),
    "whatsapp.join_group": ("invite",),
    "whatsapp.link_group": ("parent_chat", "child_chat"),
    "whatsapp.unlink_group": ("parent_chat", "child_chat"),
    "whatsapp.contact_qr": (),
    "whatsapp.set_profile_name": ("name",),
    "whatsapp.set_status": ("status",),
}

for _capability, _arguments in _TOOL_ARGUMENTS.items():
    _spec = TOOL_SPECS[_capability]
    TOOL_SPECS[_capability] = ToolSpec(
        _spec.capability,
        repeatable=_spec.repeatable,
        mutating=_spec.mutating,
        produces=_spec.produces,
        arguments=_arguments,
        required=_TOOL_REQUIRED.get(_capability, ()),
        description=_spec.description,
        executor=_spec.executor,
        permission=_spec.permission,
        destructive=_spec.destructive,
    )

_ADMIN_CAPABILITIES = frozenset({
    "admin.add_user", "admin.remove_user", "admin.list_users",
    "collections.add", "collections.remove", "collections.delete",
    "labels.delete", "work.assign", "work.unassign", "work.create_event",
    "work.create_task", "work.set_lifecycle", "work.update_event",
    "work.delete_event", "work.delete_task", "schema.set", "schema.add",
    "schema.delete", "reminders.config", "reminders.run",
    "whatsapp.add_group_members", "whatsapp.remove_group_members",
    "whatsapp.rename_group", "whatsapp.group_invite",
    "whatsapp.set_group_announce", "whatsapp.set_group_locked",
    "whatsapp.set_group_topic", "whatsapp.set_disappearing_timer",
    "whatsapp.create_group", "whatsapp.join_group", "whatsapp.leave_group",
    "whatsapp.block_contacts", "whatsapp.unblock_contacts",
    "whatsapp.pin_message", "whatsapp.revoke_message",
    "whatsapp.link_group", "whatsapp.unlink_group",
    "whatsapp.contact_qr", "whatsapp.set_profile_name",
    "whatsapp.set_status", "whatsapp.set_profile_photo",
    "whatsapp.account_info",
    "whatsapp.set_group_photo", "whatsapp.blocklist",
})
_DESTRUCTIVE_CAPABILITIES = frozenset({
    "admin.remove_user", "collections.remove", "collections.delete",
    "labels.remove", "labels.delete", "work.unassign", "work.delete_event",
    "work.delete_task", "schema.delete",
    "whatsapp.remove_group_members", "whatsapp.group_invite", "whatsapp.contact_qr",
    "whatsapp.leave_group", "whatsapp.revoke_message", "whatsapp.block_contacts",
    "whatsapp.unlink_group",
    "whatsapp.set_profile_photo",
})
_DESCRIPTIONS = {
    "work.my": "show the sender's assigned events and tasks",
    "work.undo": "undo the sender's latest reversible bot action",
    "work.overview": "show overall work or a scoped event/task overview",
    "work.list_event_tasks": "list structured tasks linked to an event",
    "work.create_event": "create an event and return its durable event ID",
    "work.create_task": "create a task, optionally linked to an event",
    "work.list_event_tasks": "list tasks linked to an event",
    "work.assign": "assign an event or task to users or member collections",
    "work.unassign": "remove assignments from an event or task",
    "collections.add": "create or add members to a subgroup",
    "collections.remove": "remove members from a subgroup",
    "collections.delete": "delete a specific subgroup or all subgroups",
    "collections.list": "list all existing subgroups",
    "collections.info": "get subgroup info and list members",
    "labels.add": "create or add members to a label",
    "labels.remove": "remove members from a label",
    "labels.delete": "delete a label",
    "whatsapp.group_info": "read current group name, topic, and member count",
    "whatsapp.group_members": "read current group members for later reasoning",
    "whatsapp.user_info": "read profile information for a resolved audience",
    "whatsapp.send_attachment": "send the attachment from the triggering message to the current group",
    "whatsapp.add_group_members": "add explicitly resolved people to the current WhatsApp group",
    "whatsapp.remove_group_members": "remove explicitly resolved people from the current WhatsApp group",
    "whatsapp.rename_group": "rename the current WhatsApp group",
    "whatsapp.group_invite": "retrieve or revoke the current group's invite link",
    "whatsapp.joined_groups": "list groups the bot is currently joined to",
    "whatsapp.community_subgroups": "list groups linked under the current community",
    "whatsapp.set_group_announce": "enable or disable announcement-only mode in the current group",
    "whatsapp.set_group_locked": "enable or disable group-settings locking in the current group",
    "whatsapp.set_group_topic": "change the current group's topic",
    "whatsapp.set_disappearing_timer": "set the current group's disappearing-message timer",
    "whatsapp.send_contact": "send an explicitly specified contact card to the current group",
    "whatsapp.send_poll": "send a bounded poll to the current group",
    "whatsapp.profile_pictures": "look up profile-picture metadata for a resolved audience",
    "whatsapp.group_join_requests": "inspect pending join requests for the current group",
    "whatsapp.linked_group_members": "inspect participants linked to the current community",
    "whatsapp.create_group": "create a new WhatsApp group with resolved participants",
    "whatsapp.join_group": "join a WhatsApp group using an explicit invite code or link",
    "whatsapp.leave_group": "leave the current WhatsApp group",
    "whatsapp.is_on_whatsapp": "check which explicitly supplied numbers have WhatsApp accounts",
    "whatsapp.block_contacts": "block explicitly resolved contacts",
    "whatsapp.unblock_contacts": "unblock explicitly resolved contacts",
    "whatsapp.pin_message": "pin the triggering message in the current group",
    "whatsapp.revoke_message": "revoke the triggering message in the current group",
    "whatsapp.set_group_photo": "set the current group's photo from an attached image",
    "whatsapp.contact_devices": "inspect WhatsApp devices for a resolved audience",
    "whatsapp.blocklist": "list the bot account's blocked contacts",
    "whatsapp.resolve_contact": "resolve a phone number or LID to WhatsApp contact identifiers",
    "whatsapp.group_info_from_link": "inspect a WhatsApp group invite link without joining it",
    "whatsapp.link_group": "link a child group to a community",
    "whatsapp.unlink_group": "unlink a child group from a community",
    "whatsapp.contact_qr": "generate or revoke the bot account's contact QR link",
    "whatsapp.set_profile_name": "change the bot account profile name",
    "whatsapp.set_status": "change the bot account status message",
    "whatsapp.set_profile_photo": "set the bot profile photo from an attached image",
    "whatsapp.account_info": "inspect the bot account identity and platform",
    "whatsapp.send": "send explicitly requested text to the current group",
    "whatsapp.reply": "reply to the triggering message with explicit text",
    "whatsapp.react": "react to the triggering message with an emoji or symbol",
}
for _capability, _spec in list(TOOL_SPECS.items()):
    TOOL_SPECS[_capability] = ToolSpec(
        _spec.capability,
        repeatable=_spec.repeatable,
        mutating=_spec.mutating,
        produces=_spec.produces,
        arguments=_spec.arguments,
        required=_spec.required,
        description=_DESCRIPTIONS.get(_capability, _spec.description),
        executor=_spec.executor,
        permission="admin" if _capability in _ADMIN_CAPABILITIES else _spec.permission,
        destructive=_capability in _DESTRUCTIVE_CAPABILITIES,
    )

CAPABILITIES = frozenset(TOOL_SPECS)


@dataclass
class AgentTrace:
    """Request-scoped execution trace used for observation and debugging."""

    request_id: str
    started_at: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, kind: str, **payload: Any) -> None:
        # Traces are INFO-level operational telemetry, not a transcript. Keep
        # only bounded control metadata so message bodies, JIDs, model JSON,
        # commands, and tool results cannot leak into ordinary logs.
        allowed = {
            "structured", "steps", "step_id", "capability", "reason",
            "compiled_steps", "result_keys",
        }
        clean = {key: payload[key] for key in allowed if key in payload}
        if "result" in payload and "result_keys" not in clean:
            result = payload["result"]
            clean["result_keys"] = sorted(result) if isinstance(result, dict) else []
        self.events.append({"kind": kind, "at": time.time(), **clean})

    def summary(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "duration_ms": round((time.time() - self.started_at) * 1000),
            "events": self.events,
        }


def render_tool_catalog() -> str:
    """Render the canonical tool registry for the planner prompt."""
    lines = ["Available registered capabilities:"]
    for capability in sorted(TOOL_SPECS):
        spec = TOOL_SPECS[capability]
        signature = ", ".join(spec.arguments) or "no arguments"
        flags = []
        if spec.mutating:
            flags.append("mutating")
        if spec.produces:
            flags.append("produces=" + ",".join(sorted(spec.produces)))
        flags.append("permission=" + spec.permission)
        if spec.destructive:
            flags.append("destructive")
        suffix = f" [{'; '.join(flags)}]" if flags else ""
        description = f" — {spec.description}" if spec.description else ""
        lines.append(f"- {capability}({signature}){suffix}{description}")
    return "\n".join(lines)


def validate_tool_arguments(capability: str, arguments: dict) -> str | None:
    spec = tool_spec(capability)
    for required_key in spec.required:
        key = required_key.removesuffix("[]").removesuffix("?")
        value = arguments.get(key)
        if value is None or value == "" or value == [] or value == {}:
            # Target can be expressed through the canonical target object.
            if key == "target":
                if arguments.get("target") or any(
                    arguments.get(target_key) is not None
                    for target_key in ("target_id", "target_name", "event_id", "task_id")
                ):
                    continue
                if capability in {"work.assign", "work.unassign"}:
                    continue
            if key == "event" and any(arguments.get(k) for k in ("event_id", "target_id", "target_name")):
                continue
            return f"{capability} requires argument {key}"
    return None


def tool_spec(capability: str) -> ToolSpec:
    return TOOL_SPECS.get(capability, ToolSpec(capability))


def validate_registry() -> list[str]:
    """Return configuration errors before a planner can use the registry."""
    errors: list[str] = []
    valid_executors = {"command", "direct"}
    for capability, spec in TOOL_SPECS.items():
        if spec.capability != capability:
            errors.append(f"{capability}: capability name mismatch")
        if spec.executor not in valid_executors:
            errors.append(f"{capability}: unknown executor {spec.executor}")
        if not spec.permission:
            errors.append(f"{capability}: missing permission policy")
        schema_names = {
            argument.rstrip("?").removesuffix("[]")
            for argument in spec.arguments
        }
        missing = [key for key in spec.required if key not in schema_names]
        if missing:
            errors.append(f"{capability}: required fields absent from schema: {', '.join(missing)}")
        if any(not isinstance(value, str) or not value.strip() for value in spec.arguments):
            errors.append(f"{capability}: argument names must be non-empty strings")
    return errors


def validate_plan_preflight(plan: list[dict]) -> str | None:
    """Validate plan dependencies before the executor can mutate state."""
    seen: set[str] = set()
    produced_by: dict[str, frozenset[str]] = {}
    for index, step in enumerate(plan, start=1):
        step_id = step.get("step_id") or f"step{index}"
        if step_id in seen:
            return f"duplicate plan step identifier: {step_id}"
        seen.add(step_id)
        capability = step.get("capability")
        if not isinstance(capability, str):
            return f"plan step {step_id} has no capability"
        if capability not in TOOL_SPECS:
            return f"plan step {step_id} uses an unregistered capability: {capability}"
        arguments = step.get("arguments", {})
        if not isinstance(arguments, dict):
            return f"plan step {step_id} arguments must be an object"
        argument_error = validate_tool_arguments(capability, arguments)
        if argument_error:
            return f"plan step {step_id}: {argument_error}"
        for value in _walk_values(arguments):
            if isinstance(value, str):
                for reference in _REFERENCE_RE.findall(value):
                    if reference not in seen:
                        return f"plan step {step_id} references a later or unknown step: {reference}"
                    field = (
                        value[1:].split(".", 1)[1].split(".", 1)[0]
                        if value.startswith("$") and "." in value
                        else ""
                    )
                    producer_fields = produced_by.get(reference, frozenset({"task_id", "event_id"}))
                    if field and reference in produced_by and field not in producer_fields:
                        return f"plan step {step_id} references unavailable output {value}"
        produced_by[step_id] = tool_spec(capability).produces
    return None


def _walk_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    else:
        yield value
