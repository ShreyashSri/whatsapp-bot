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

    handlers = [
        register_media(client, config),
        register_cards(client, config),
        register_community_tag(client, config),
        register_subgroups(client, config),
    ]
    # The incident feature owns its Flask listener and is not a MessageEv
    # handler, so start it after the four existing message features.
    register_incidents(client, config)

    def dispatch(message: MessageEv) -> None:
        for handler in handlers:
            if handler:
                handler(client, message)

    return dispatch
