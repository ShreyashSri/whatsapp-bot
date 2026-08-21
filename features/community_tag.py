"""Community Group Tagging Feature.

When someone @mentions a group in any community chat, the bot sends a single
notification message that silently pings every member of that group.  Members
receive a notification but the message itself only shows the group name — no
individual @names are displayed.

Requirements:
  - The bot must be a member of the mentioned group.
  - Works for any group the bot has joined within the community.
"""

from __future__ import annotations

import logging
import weakref
from typing import TYPE_CHECKING

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
    ContextInfo,
    ExtendedTextMessage,
    Message,
)
from neonize.utils.jid import Jid2String, JIDToNonAD
from db.auth import normalize_jid
from features.text import public_text

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_context_info(message: "MessageEv") -> ContextInfo | None:
    """Extract ContextInfo from the incoming message, regardless of type.

    WhatsApp wraps the real payload in ephemeral / viewOnce containers.
    Rather than manually unwrapping every layer we iterate over protobuf
    fields until we find a ContextInfo.
    """
    msg = message.Message

    # Walk through wrapper layers (ephemeral, viewOnce, etc.)
    for _ in range(5):  # safety limit
        found_wrapper = False
        for field_desc, value in msg.ListFields():
            name = field_desc.name
            # Unwrap known wrapper messages
            if name in (
                "ephemeralMessage",
                "viewOnceMessage",
                "viewOnceMessageV2",
                "documentWithCaptionMessage",
                "groupMentionedMessage",
            ):
                inner = getattr(value, "message", None)
                if inner and inner.ListFields():
                    msg = inner
                    found_wrapper = True
                    break
        if not found_wrapper:
            break

    # Now look for contextInfo on the actual message type
    for field_desc, value in msg.ListFields():
        if field_desc.name.endswith("Message"):
            ctx = getattr(value, "contextInfo", None)
            if ctx is not None and ctx.ListFields():
                return ctx
    return None


def _get_group_mentions(ctx: ContextInfo) -> list[tuple[str, str]]:
    """Extract (groupJID, groupSubject) pairs from contextInfo.groupMentions."""
    mentions = []
    for gm in getattr(ctx, "groupMentions", []):
        jid = getattr(gm, "groupJID", "")
        subject = getattr(gm, "groupSubject", "")
        if jid:
            mentions.append((jid, subject))
    return mentions


_self_jids_cache: "weakref.WeakKeyDictionary[NewClient, set[str]]" = weakref.WeakKeyDictionary()


def get_client_self_jids(client: "NewClient") -> set[str]:
    """Return the paired account's phone and LID JIDs for safety filtering.

    Cached per client instance after the first successful lookup: get_me()
    blocks on live transport state, and every message handler calls this, so
    an unresolved transport hiccup would otherwise wedge the single-threaded
    dispatch worker on every message indefinitely.
    """
    cached = _self_jids_cache.get(client)
    if cached is not None:
        return cached

    try:
        device = client.get_me()
    except Exception:
        log.debug("Could not resolve the bot's own JIDs", exc_info=True)
        return set()

    result: set[str] = set()
    for field in ("JID", "LID"):
        value = getattr(device, field, None)
        if isinstance(value, str):
            raw = value
        else:
            user = getattr(value, "User", None)
            server = getattr(value, "Server", None)
            raw = (
                f"{user}@{server}"
                if isinstance(user, str) and isinstance(server, str) and user and server
                else ""
            )
        normalized = normalize_jid(raw)
        if normalized:
            result.add(normalized)
    if result:
        _self_jids_cache[client] = result
    return result


def get_group_member_jids(client: "NewClient", group_jid) -> list[str]:
    """Return the current human participant JIDs for a joined WhatsApp group.

    Community tagging already needs this information.  Keep the lookup in a
    shared helper so other features can resolve semantic targets such as
    ``everyone in this group`` without exposing participant data to the LLM.
    The paired account is always excluded before the list leaves this helper.
    """
    groups = client.get_joined_groups()
    self_jids = get_client_self_jids(client)
    wanted = str(group_jid)
    wanted_user = getattr(group_jid, "User", "")
    for group in groups:
        candidate = getattr(group, "JID", None)
        if candidate is None:
            continue
        candidate_user = getattr(candidate, "User", "")
        if candidate_user and wanted_user:
            matches = candidate_user == wanted_user
        else:
            matches = Jid2String(JIDToNonAD(candidate)) == wanted
        if not matches:
            continue

        members: list[str] = []
        for participant in getattr(group, "Participants", []):
            jid = getattr(participant, "JID", None)
            if jid is None or not getattr(jid, "User", ""):
                continue
            normalized = normalize_jid(Jid2String(JIDToNonAD(jid)))
            if normalized and normalized not in self_jids:
                members.append(normalized)
        return list(dict.fromkeys(members))
    return []


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat

        # Only process group messages
        if getattr(chat, "Server", "") != "g.us":
            return

        # Ignore our own messages to prevent loops.
        if message.Info.MessageSource.IsFromMe:
            return

        ctx = _get_context_info(message)
        if ctx is None:
            return

        group_mentions = _get_group_mentions(ctx)
        if not group_mentions:
            return

        log.info("Detected group mentions: %s", group_mentions)

        # Cache joined groups so we only fetch once per message
        try:
            groups = client.get_joined_groups()
        except Exception as exc:
            log.error("Failed to fetch joined groups: %s", exc)
            return

        # Build a lookup: User part -> GroupInfo
        groups_by_user = {}
        for g in groups:
            user = getattr(g.JID, "User", "")
            if user:
                groups_by_user[user] = g

        for group_jid_str, group_subject in group_mentions:
            target_user = group_jid_str.split("@")[0]
            ginfo = groups_by_user.get(target_user)

            if ginfo is None:
                log.warning(
                    "Bot is not in group %s (%s) — cannot tag its members.",
                    group_subject, group_jid_str,
                )
                continue

            # Collect participant JIDs as full strings (user@server)
            mentioned_jids: list[str] = []

            try:
                mentioned_jids = get_group_member_jids(client, ginfo.JID)
            except Exception as exc:
                log.error("Failed to resolve participants for group %s: %s", group_subject, exc)
                continue

            if not mentioned_jids:
                log.warning("No participants found in group %s", group_subject)
                continue

            # Clean message — no @names visible, but mentionedJID ensures notifications
            text = f"📢 Tagging all members of {public_text(group_subject or 'the community group', limit=120)}"

            # Construct the protobuf Message directly so we have full control
            # over mentionedJID (avoids the regex-based ghost_mentions hack).
            proto_msg = Message(
                extendedTextMessage=ExtendedTextMessage(
                    text=text,
                    contextInfo=ContextInfo(
                        mentionedJID=mentioned_jids,
                    ),
                ),
            )

            try:
                client.send_message(chat, proto_msg)
                log.info(
                    "Sent community tag for group %s — %d members mentioned.",
                    group_subject, len(mentioned_jids),
                )
            except Exception as exc:
                log.error("Failed to send community tag message: %s", exc)

    log.info("✅ Community tag feature registered")
    return on_message
