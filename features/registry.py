"""Runtime feature registration and message dispatch."""

from __future__ import annotations

import logging
from typing import Callable

from neonize.events import MessageEv

log = logging.getLogger(__name__)


def register_features(client, config: dict) -> Callable:
    """Register features in the existing order and return the dispatcher."""
    from features.media import register as register_media
    from features.cards import register as register_cards
    from features.community_tag import register as register_community_tag
    from features.subgroups import register as register_subgroups
    from features.incidents import register as register_incidents
    from features.admin import register as register_admin
    from features.help import register as register_help
    from features.work import register as register_work
    from features.reminders import register as register_reminders
    from features.reports import register as register_reports
    from features.labels import register as register_labels

    work_handler = register_work(client, config)

    handlers = [
        register_admin(client, config),
        register_help(client, config),
        register_media(client, config),
        register_cards(client, config),
        register_community_tag(client, config),
        register_subgroups(client, config),
        register_reminders(client, config),
        register_reports(client, config),
        register_labels(client, config),
    ]
    # The incident feature owns its Flask listener and is not a MessageEv
    # handler, so start it after the four existing message features.
    register_incidents(client, config)

    def dispatch(message: MessageEv) -> None:
        # Workload commands have one owner. Returning here is the central
        # collision guard for the historical events/tasks handlers.
        try:
            if work_handler and work_handler(client, message):
                return
        except Exception:
            log.exception("work handler failed")
        for handler in handlers:
            if not handler:
                continue
            # One failing feature must not silence the features registered
            # after it, and the failure has to be visible in the logs.
            try:
                handler(client, message)
            except Exception:
                log.exception("feature handler %s failed",
                              getattr(handler, "__module__", handler))

    return dispatch
