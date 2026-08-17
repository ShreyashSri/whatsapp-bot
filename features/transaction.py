"""Capabilities whose database effects can share one NL plan transaction."""

TRANSACTIONAL_PLAN_CAPABILITIES = frozenset({
    "collections.add", "collections.remove", "collections.delete",
    "collections.list", "collections.info", "collections.tag", "labels.add", "labels.remove", "labels.delete",
    "work.assign", "work.unassign", "work.create_event", "work.create_task",
    "work.my", "work.overview", "work.list_event_tasks",
    "work.update_event", "work.delete_event", "work.delete_task",
    "work.set_lifecycle",
    # Pure DB reads/mutations with no WhatsApp-external side effect, same
    # kind as work.set_lifecycle above -- omitting them here isn't a
    # deliberate safety boundary, it just blocks a plan that legitimately
    # combines one of these with another work.* capability (e.g. "complete
    # task 5 and reassign it to @Bob") with the wrong error message
    # ("can't combine database work with external WhatsApp changes") for a
    # request that never touches anything external.
    "work.history", "work.status", "work.start", "work.complete", "work.update",
    "media.add", "media.remove", "media.todo", "media.posted", "media.unposted",
    "media.posted_list",
})
