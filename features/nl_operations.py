"""Direct operation adapters for semantic natural-language execution.

These functions are the mutation boundary shared by the NL executor and the
legacy handlers' domain services. They accept resolved entities/JIDs directly;
they never manufacture a WhatsApp command or mention context.
"""

from __future__ import annotations

from typing import Callable


# Mutating capabilities use this module as their single semantic execution
# boundary. Legacy command handlers call the same feature-domain services.
DIRECT_CAPABILITIES = frozenset({
    "collections.add",
    "collections.remove",
    "labels.add",
    "labels.remove",
    "work.assign",
    "work.unassign",
    "work.create_event",
    "work.create_task",
})


def is_direct_capability(capability: str) -> bool:
    return capability in DIRECT_CAPABILITIES


def _parse_date(value):
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("dates must use YYYY-MM-DD") from exc


def execute_work_creation(client, message, intent: dict, factory) -> dict | None:
    """Create an event/task and return its durable identifiers to the plan."""
    source = message.Info.MessageSource
    chat = source.Chat
    capability = intent["capability"]
    arguments = intent.get("arguments", {})
    from db.auth import audit, gate

    actor = gate(factory, source.Sender, client, chat, "admin", capability)
    if not actor:
        return None

    try:
        if capability == "work.create_event":
            from db.event_store import EventStore
            labels = arguments.get("labels") or []
            if isinstance(labels, str):
                labels = [item.strip() for item in labels.split(",") if item.strip()]
            event = EventStore(factory).create_event(
                name=str(arguments.get("name") or "").strip(),
                type=str(arguments.get("type") or "organization").strip(),
                category=str(arguments.get("category") or "other").strip(),
                description=arguments.get("description"),
                labels=labels,
                start_date=_parse_date(arguments.get("start")),
                end_date=_parse_date(arguments.get("end")),
                status="active",
            )
            audit(factory, actor, "event.create", "natural_language", {
                "event_id": event["id"], "name": event["name"],
            })
            client.send_message(chat, f"✅ Event `{event['id']}` created: *{event['name']}*")
            return {"event_id": event["id"], "event": event}

        from db.task_store import TaskStore
        event_id = arguments.get("event_id")
        task = TaskStore(factory).create(
            title=str(arguments.get("title") or "").strip(),
            created_by_jid=source.Sender,
            description=arguments.get("description"),
            event_id=int(event_id) if event_id is not None else None,
            due_date=_parse_date(arguments.get("due")),
            priority=str(arguments.get("priority") or "medium").lower(),
        )
        audit(factory, actor, "task.create", "natural_language", {
            "task_id": task.id, "title": task.title, "event_id": task.event_id,
        })
        parent = f" under event {task.event_id}" if task.event_id else ""
        client.send_message(chat, f"✅ Task `{task.id}` created{parent}: *{task.title}*")
        return {"task_id": task.id, "event_id": task.event_id, "task": {
            "id": task.id, "title": task.title, "event_id": task.event_id,
        }}
    except (TypeError, ValueError) as exc:
        client.send_message(chat, f"⚠️ {exc}")
        return None

def execute_collection_mutation(
    client,
    message,
    intent: dict,
    members: list[str],
    factory,
    resolve_collection: Callable[[object, str], str | None],
) -> bool:
    """Execute a subgroup add/remove operation with concrete members."""
    source = message.Info.MessageSource
    chat = source.Chat
    action = intent["capability"].split(".", 1)[1]
    collection = resolve_collection(
        factory, intent.get("arguments", {}).get("collection")
    )
    if not collection:
        client.send_message(chat, "⚠️ I couldn't resolve the subgroup name.")
        return True
    from db.auth import gate
    from db.subgroup_store import SubgroupStore
    from features.subgroups import add_subgroup_members, remove_subgroup_members

    actor = gate(factory, source.Sender, client, chat, "admin", f"subgroup.{action}")
    if not actor:
        return True
    try:
        store = SubgroupStore(factory)
        if action == "add":
            added, total = add_subgroup_members(store, collection, members)
            if added:
                client.send_message(
                    chat,
                    f"✅ Added {added} member(s) to @{collection} (total: {total}).",
                )
            else:
                client.send_message(
                    chat,
                    f"ℹ️ All mentioned users are already in @{collection} ({total} members).",
                )
        else:
            removed, remaining, deleted = remove_subgroup_members(
                store, collection, members
            )
            if deleted:
                client.send_message(
                    chat,
                    f"🗑️ Subgroup @{collection} deleted (no members remaining).",
                )
            else:
                client.send_message(
                    chat,
                    f"✅ Removed {removed} member(s) from @{collection} "
                    f"({remaining} remaining).",
                )
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")
    return True


def execute_label_mutation(
    client,
    message,
    intent: dict,
    members: list[str],
    factory,
    resolve_collection: Callable[[object, str], str | None],
) -> bool:
    """Execute label add/remove using direct resolved members."""
    source = message.Info.MessageSource
    chat = source.Chat
    action = intent["capability"].split(".", 1)[1]
    collection = resolve_collection(
        factory, intent.get("arguments", {}).get("collection")
    )
    if not collection:
        client.send_message(chat, "⚠️ I couldn't resolve the label name.")
        return True
    from db.auth import audit, gate, jid_user, normalize_jid
    from db.subgroup_store import SubgroupStore
    from features.labels import add_label_members, remove_label_members

    actor = gate(factory, source.Sender, client, chat, "member", f"label.{action}")
    if not actor:
        return True

    targets = [normalize_jid(member) for member in members if normalize_jid(member)]
    if actor.role != "admin":
        sender_user = jid_user(source.Sender)
        if any(jid_user(target) != sender_user for target in targets):
            client.send_message(
                chat,
                "⛔ You can only add or remove yourself. "
                "Ask an admin to change someone else's labels.",
            )
            return True
    try:
        store = SubgroupStore(factory)
        if action == "add":
            added, total = add_label_members(store, collection, targets)
            audit(
                factory,
                actor,
                "label.assign",
                "natural_language",
                {"label": collection, "added": added},
            )
            client.send_message(
                chat,
                f"✅ Label {collection} now has {total} member(s)."
                + (f" Added {len(added)}." if added else " No new members."),
            )
        else:
            removed, deleted = remove_label_members(store, collection, targets)
            audit(
                factory,
                actor,
                "label.remove",
                "natural_language",
                {"label": collection, "removed": removed},
            )
            client.send_message(
                chat,
                f"✅ Removed {removed} member(s) from {collection}."
                + ("" if not deleted else " Label deleted (now empty)."),
            )
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {exc}")
    return True


def execute_work_assignment(
    client,
    message,
    intent: dict,
    members: list[str],
    factory,
    resolve_work_target: Callable[[dict], str | None],
) -> bool:
    """Assign/unassign a work item to concrete resolved members."""
    source = message.Info.MessageSource
    chat = source.Chat
    from db.auth import audit, gate, normalize_jid
    from db.work_store import WorkStore

    actor = gate(factory, source.Sender, client, chat, "admin", "work.assign")
    if not actor:
        return True
    reference = resolve_work_target(intent.get("arguments", {}))
    if not reference:
        client.send_message(chat, "⚠️ I couldn't resolve the event or task.")
        return True
    target_type, target_id = reference.split()
    store = WorkStore(factory)
    targets = list(dict.fromkeys(normalize_jid(member) for member in members if normalize_jid(member)))
    if not targets:
        client.send_message(chat, "⚠️ I couldn't resolve any assignees.")
        return True
    action = intent["capability"].split(".", 1)[1]
    try:
        if action == "assign":
            rows = store.assign_many(target_type, int(target_id), targets)
            assigned = [row["user_jid"].split("@", 1)[0] for row in rows]
            audit(
                factory,
                actor,
                f"{target_type}.assign",
                "natural_language",
                {"target_id": int(target_id), "users": targets},
            )
            client.send_message(
                chat,
                f"✅ Assigned {target_type} {target_id} to "
                + ", ".join(f"@{name}" for name in assigned)
                + ".",
            )
        else:
            removed = store.unassign_many(target_type, int(target_id), targets)
            audit(
                factory,
                actor,
                f"{target_type}.unassign",
                "natural_language",
                {"target_id": int(target_id), "users": removed},
            )
            client.send_message(
                chat,
                f"✅ Removed {len(removed)} assignment(s) from "
                f"{target_type} {target_id}."
                if removed
                else "📭 No matching assignments found.",
            )
    except Exception as exc:
        client.send_message(chat, f"⚠️ {exc}")
    return True
