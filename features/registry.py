"""Runtime feature registration and message dispatch."""

from __future__ import annotations

import logging
from typing import Callable

from neonize.events import MessageEv

log = logging.getLogger(__name__)


def register_features(client, config: dict) -> Callable:
    """Register features in the existing order and return the dispatcher."""
    from features.natural_language import register as register_natural_language
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
    natural_language_handler = register_natural_language(client, config)

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

    def dispatch(message: MessageEv, session_factory=None, client_override=None) -> None:
        # Natural-language messages are translated into a normal command and
        # re-enter this dispatcher. This keeps all existing command ownership,
        # authorization, validation, and auditing in one path.
        active_client = client_override or client
        if session_factory is not None:
            try:
                message._pbbot_session_factory = session_factory
            except (AttributeError, TypeError):
                pass
        try:
            if natural_language_handler and natural_language_handler(active_client, message, dispatch):
                return
        except Exception:
            log.exception("natural-language handler failed")
            return
        # Workload commands have one owner. Returning here is the central
        # collision guard for the historical events/tasks handlers.
        try:
            if work_handler and work_handler(active_client, message):
                if session_factory is not None and getattr(session_factory, "failed", False):
                    return
                return
        except Exception:
            if session_factory is not None and hasattr(session_factory, "mark_failed"):
                session_factory.mark_failed()
            log.exception("work handler failed")
        for handler in handlers:
            if not handler:
                continue
            # One failing feature must not silence the features registered
            # after it, and the failure has to be visible in the logs.
            try:
                handler(active_client, message)
            except Exception:
                if session_factory is not None and hasattr(session_factory, "mark_failed"):
                    session_factory.mark_failed()
                log.exception("feature handler %s failed",
                              getattr(handler, "__module__", handler))

    return dispatch
