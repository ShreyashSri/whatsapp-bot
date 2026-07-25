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
from typing import TYPE_CHECKING

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
    ContextInfo,
    ExtendedTextMessage,
    Message,
)
from neonize.utils.jid import Jid2String, JIDToNonAD

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

        # TEST-ONLY GUARD: review/remove for production routing.
        # Ignore our own messages to prevent loops
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

            for p in getattr(ginfo, "Participants", []):
                jid = getattr(p, "JID", None)
                if jid is None:
                    continue
                user = getattr(jid, "User", "")
                if not user:
                    continue
                # Convert to non-AD form and then to string
                mentioned_jids.append(Jid2String(JIDToNonAD(jid)))

            if not mentioned_jids:
                log.warning("No participants found in group %s", group_subject)
                continue

            # Clean message — no @names visible, but mentionedJID ensures notifications
            text = f"📢 Tagging all members of {group_subject}"

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
