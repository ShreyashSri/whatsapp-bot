"""Capabilities whose database effects can share one NL plan transaction."""

TRANSACTIONAL_PLAN_CAPABILITIES = frozenset({
    "collections.add", "collections.remove", "collections.delete",
    "collections.list", "collections.info", "labels.add", "labels.remove", "labels.delete",
    "work.assign", "work.unassign", "work.create_event", "work.create_task",
    "work.my", "work.overview", "work.list_event_tasks",
    "work.update_event", "work.delete_event", "work.delete_task",
    "work.set_lifecycle",
})
