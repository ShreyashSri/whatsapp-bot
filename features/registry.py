"""Runtime feature registration and message dispatch."""

from __future__ import annotations

from typing import Callable

from neonize.events import MessageEv


def register_features(client, config: dict) -> Callable:
    """Register features in the existing order and return the dispatcher."""
    from features.media import register as register_media
    from features.cards import register as register_cards
    from features.community_tag import register as register_community_tag
    from features.subgroups import register as register_subgroups
    from features.incidents import register as register_incidents
    from features.admin import register as register_admin
    from features.help import register as register_help
    from features.events import register as register_events
    from features.events_management import register as register_events_management

    handlers = [
        register_admin(client, config),
        register_help(client, config),
        register_media(client, config),
        register_cards(client, config),
        register_community_tag(client, config),
        register_subgroups(client, config),
        register_events(client, config),
        register_events_management(client, config),
    ]
    # The incident feature owns its Flask listener and is not a MessageEv
    # handler, so start it after the four existing message features.
    register_incidents(client, config)

    def dispatch(message: MessageEv) -> None:
        for handler in handlers:
            if handler:
                handler(client, message)

    return dispatch
