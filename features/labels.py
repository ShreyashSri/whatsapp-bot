"""User labels (PRS 7.2).

Labels reuse the existing subgroup table: a subgroup is already a named set of
users, which is exactly a label with its members. That keeps one store for both
and means a label is also mentionable, so `@backend` still pings the group.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.auth import audit, gate, jid_user, normalize_jid
from db.subgroup_store import SubgroupStore
from features.subgroups import _NAME_RE, _get_mentioned_jids, _get_text
from features.text import public_error, public_text, split_command_fields

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)
LABEL_CMDS = ("!labels", "!label")


def _valid_name(raw: str) -> str:
    name = raw.strip().lstrip("@").lower()
    if not _NAME_RE.match(name):
        raise ValueError("label names must be 2-32 characters: letters, digits, hyphens, or underscores")
    return name


def _labels_of(store: SubgroupStore, jid: str) -> list[str]:
    wanted = jid_user(jid)
    return sorted(name for name, members in store.read().items()
                  if any(jid_user(member) == wanted for member in members))


def add_label_members(
    store: SubgroupStore,
    name: str,
    targets: list[str],
) -> tuple[list[str], int]:
    """Create/update a label from concrete runtime JIDs."""
    name = _valid_name(name)
    normalized_targets: list[str] = []
    existing_users: set[str] = set()
    for target in targets:
        normalized = normalize_jid(target)
        if normalized and jid_user(normalized) not in existing_users:
            normalized_targets.append(normalized)
            existing_users.add(jid_user(normalized))

    data = store.read()
    existing_users = {jid_user(jid) for jid in data.get(name, [])}
    normalized_targets = [
        target for target in normalized_targets
        if jid_user(target) not in existing_users
    ]
    result = store.add_members(name, normalized_targets)
    if isinstance(result, tuple) and len(result) == 2:
        return result
    # Compatibility for lightweight store doubles and old integrations.
    members = data.get(name, [])
    added: list[str] = []
    existing_users = {jid_user(jid) for jid in members}
    for target in normalized_targets:
        if jid_user(target) not in existing_users:
            members.append(target)
            existing_users.add(jid_user(target))
            added.append(target)
    data[name] = members
    store.write(data)
    return added, len(members)


def remove_label_members(
    store: SubgroupStore,
    name: str,
    targets: list[str],
) -> tuple[int, bool]:
    """Remove concrete runtime JIDs and report (removed, label_deleted)."""
    name = _valid_name(name)
    if not targets:
        raise ValueError("mention at least one user to remove from the label")
    data = store.read()
    if name not in data:
        raise ValueError("label not found")
    wanted = {jid_user(jid) for jid in targets if normalize_jid(jid)}
    result = store.remove_members(name, wanted)
    if isinstance(result, tuple) and len(result) == 3:
        removed, _remaining, deleted = result
        return removed, deleted
    # Compatibility for lightweight store doubles and old integrations.
    kept = [jid for jid in data[name] if jid_user(jid) not in wanted]
    removed = len(data[name]) - len(kept)
    if kept:
        data[name] = kept
    else:
        del data[name]
    store.write(data)
    return removed, not kept


def register(client: "NewClient", config: dict) -> callable:
    factory = config.get("db_session_factory")
    if factory is None:
        raise RuntimeError("Labels feature requires db_session_factory")

    def on_message(client: "NewClient", message) -> None:
        if not message.Info or not message.Info.MessageSource:
            return
        source = message.Info.MessageSource
        chat = source.Chat
        if getattr(chat, "Server", "") != "g.us":
            return
        body = _get_text(message)
        command, _, args = body.partition(" ")
        if command.lower() not in LABEL_CMDS:
            return

        args = args.strip()
        action, _, rest = args.partition(" ")
        action, rest = action.lower(), rest.strip()
        mentions = _get_mentioned_jids(message)
        actor = gate(factory, source.Sender, client, chat, "member", f"label.{action or 'list'}")
        if not actor:
            return
        is_admin = actor.role == "admin"
        self_jid = normalize_jid(source.Sender)
        store = SubgroupStore(factory)

        try:
            if action in ("of", "for") or (mentions and action not in
                                           ("create", "add", "assign", "remove", "delete")):
                target = normalize_jid(mentions[0]) if mentions else normalize_jid(source.Sender)
                names = _labels_of(store, target)
                public_target = "member" if target.endswith("@lid") else jid_user(target)
                client.send_message(chat, f"🏷️ Labels for @{public_target}: "
                                          + (", ".join(f"`{public_text(name, limit=80)}`" for name in names) if names else "_none_"))
                return

            if not action or action == "list":
                data = store.read()
                if not data:
                    client.send_message(chat, "📭 No labels yet. Create one with `!labels create <name> | @user`.")
                    return
                lines = [f"🏷️ *Labels ({len(data)})*"]
                lines += [f"• `{public_text(name, limit=80)}` — {len(members)} member(s)" for name, members in sorted(data.items())]
                client.send_message(chat, "\n".join(lines))
                return

            label_parts = split_command_fields(rest, limit=1)
            head = label_parts[0]
            member_text = label_parts[1] if len(label_parts) > 1 else ""
            name = _valid_name(head)
            if action == "delete":
                if not is_admin:
                    client.send_message(chat, "⛔ Only administrators can delete a label.")
                    return
                if not store.delete(name):
                    client.send_message(chat, f"📭 No label named `{public_text(name, limit=80)}`.")
                    return
                audit(factory, actor, "label.delete", "whatsapp", {"label": name})
                client.send_message(chat, f"🗑️ Label `{public_text(name, limit=80)}` deleted.")
                return

            targets = [normalize_jid(jid) for jid in mentions if normalize_jid(jid)]
            if not is_admin:
                # Anyone may opt themselves into or out of a label, but only an
                # admin may move other people.
                if not targets and getattr(message, "_pbbot_nl_no_target_mentions", False) is not True:
                    targets = [self_jid]
                if targets and [jid_user(jid) for jid in targets] != [jid_user(self_jid)]:
                    client.send_message(chat, "⛔ You can only add or remove yourself. "
                                              "Ask an admin to change someone else's labels.")
                    return
            if action in ("create", "add", "assign"):
                from features.community_tag import get_client_self_jids
                self_jids = get_client_self_jids(client)
                targets = [jid for jid in targets if jid not in self_jids]
                if not targets:
                    client.send_message(chat, "⚠️ The bot cannot be added to a label or subgroup.")
                    return
                added, total = add_label_members(store, name, targets)
                audit(factory, actor, "label.create" if action == "create" else "label.assign",
                      "whatsapp", {"label": name, "added": added})
                client.send_message(chat, f"✅ Label `{public_text(name, limit=80)}` now has {total} member(s)."
                                   + (f" Added {len(added)}." if added else " No new members."))
                return

            if action == "remove":
                removed, deleted = remove_label_members(store, name, targets)
                audit(factory, actor, "label.remove", "whatsapp", {"label": name, "removed": removed})
                client.send_message(chat, f"✅ Removed {removed} member(s) from `{public_text(name, limit=80)}`."
                                    + ("" if not deleted else " Label deleted (now empty)."))
                return

            client.send_message(chat, "Usage: `!labels [list|create|assign|remove|delete] <name> | @user`")
        except Exception as exc:
            log.info("label command failed: %s", exc)
            client.send_message(chat, f"⚠️ {public_error(exc, 'I could not update that label.')}")

    return on_message
