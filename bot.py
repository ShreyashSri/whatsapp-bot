#!/usr/bin/env python3
"""WhatsApp Bot — main entry point.

Initialises a neonize WhatsApp client and registers all features.
To add a new feature, create ``features/your_feature.py`` with a
``register(client, config)`` function and import it below.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neonize.client import NewClient
from neonize.events import ConnectedEv, PairStatusEv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

load_dotenv()


def _parse_group_ids(*env_keys: str) -> set[str]:
    """Parse comma-separated group IDs from one or more env vars."""
    ids: set[str] = set()
    for key in env_keys:
        val = os.getenv(key, "")
        for gid in val.split(","):
            gid = gid.strip()
            if gid:
                ids.add(gid)
    return ids


GROUP_IDS = _parse_group_ids("GROUP_ID", "GROUP_IDS")
MEDIA_GROUP_ID = os.getenv("MEDIA_GROUP_ID", "").strip() or None
INCIDENT_GROUP_ID = os.getenv("INCIDENT_GROUP_ID", "").strip() or None
INCIDENT_PORT = int(os.getenv("INCIDENT_PORT", "8081"))

# Session database for neonize (persists WhatsApp login across restarts)
SESSION_DB = Path.cwd() / "neonize.db"

# ---------------------------------------------------------------------------
# Build config dict shared across features
# ---------------------------------------------------------------------------

config: dict = {
    "group_ids": GROUP_IDS,
    "media_group_id": MEDIA_GROUP_ID,
    "incident_group_id": INCIDENT_GROUP_ID,
    "incident_port": INCIDENT_PORT,
}

# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

client = NewClient(str(SESSION_DB))


@client.event(PairStatusEv)
def on_pair_status(_client: NewClient, event: PairStatusEv):
    log.info("📱 Pair status: %s", event)


@client.event(ConnectedEv)
def on_connected(_client: NewClient, _event: ConnectedEv):
    log.info("✅ Bot connected to WhatsApp — all features active")


# ---------------------------------------------------------------------------
# Register features — add new imports here to extend the bot
# ---------------------------------------------------------------------------

from features.media import register as register_media        # noqa: E402
from features.cards import register as register_cards        # noqa: E402
from features.incidents import register as register_incidents  # noqa: E402
from features.community_tag import register as register_community_tag  # noqa: E402

media_handler = register_media(client, config)
cards_handler = register_cards(client, config)
community_tag_handler = register_community_tag(client, config)
register_incidents(client, config)

from neonize.events import MessageEv

@client.event(MessageEv)
def on_message(client: "NewClient", message: MessageEv):
    if media_handler:
        media_handler(client, message)
    if cards_handler:
        cards_handler(client, message)
    if community_tag_handler:
        community_tag_handler(client, message)

# ---------------------------------------------------------------------------
# Global error handling
# ---------------------------------------------------------------------------


def _excepthook(exc_type, exc_value, exc_tb):
    log.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _excepthook

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Starting WhatsApp bot...")
    log.info("Groups: %s", GROUP_IDS or "(none)")
    log.info("Media group: %s", MEDIA_GROUP_ID or "(not set)")
    log.info("Incident group: %s", INCIDENT_GROUP_ID or "(not set)")
    client.connect()
