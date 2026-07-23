"""Custom Subgroups Feature.

Lets users create named subgroups (e.g. ``blogmaintainers``) and populate
them with WhatsApp users.  When someone writes ``@blogmaintainers`` in any
group the bot is in, every member of that subgroup receives a silent
notification (ghost mention) — their names do not appear in the message.

Subgroups are global (not per-group) and persisted in ``subgroups.json``.

Commands:
  !add-subgroup <name> | @user1 @user2 …
  !remove-from-subgroup <name> | @user1 @user2 …
  !delete-subgroup <name>
  !list-subgroups
  !subgroup-info <name>
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
    ContextInfo,
    ExtendedTextMessage,
    Message,
)

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

# Subgroup names: alphanumeric, hyphens, underscores (2-32 chars)
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,31}$")

# ---------------------------------------------------------------------------
# Persistence (subgroups.json)
# ---------------------------------------------------------------------------

_SUBGROUPS_FILE: Path = Path.cwd() / "subgroups.json"


def _read_subgroups() -> dict[str, list[str]]:
    """Load subgroups from disk.  Returns {name: [jid, …]}."""
    if not _SUBGROUPS_FILE.exists():
        return {}
    try:
        data = json.loads(_SUBGROUPS_FILE.read_text())
        if not isinstance(data, dict):
            return {}
        # Sanitise: drop any entries that aren't lists of strings
        return {
            k: v for k, v in data.items()
            if isinstance(v, list) and all(isinstance(j, str) for j in v)
        }
    except (json.JSONDecodeError, OSError) as exc:
        log.error("subgroups.json corrupt, starting fresh: %s", exc)
        return {}


def _write_subgroups(data: dict[str, list[str]]) -> None:
    _SUBGROUPS_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Message text extraction
# ---------------------------------------------------------------------------

def _get_text(message: "MessageEv") -> str:
    """Extract plain text body from a message."""
    msg = message.Message
    if msg.conversation:
        return msg.conversation.strip()
    if msg.extendedTextMessage and msg.extendedTextMessage.text:
        return msg.extendedTextMessage.text.strip()
    if msg.imageMessage and msg.imageMessage.caption:
        return msg.imageMessage.caption.strip()
    return ""


def _get_mentioned_jids(message: "MessageEv") -> list[str]:
    """Extract mentionedJID strings from the message's contextInfo."""
    msg = message.Message

    # Walk through wrapper layers (ephemeral, viewOnce, etc.)
    for _ in range(5):
        found_wrapper = False
        for field_desc, value in msg.ListFields():
            if field_desc.name in (
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

    # Look for contextInfo on the actual message type
    for field_desc, value in msg.ListFields():
        if field_desc.name.endswith("Message"):
            ctx = getattr(value, "contextInfo", None)
            if ctx is not None and ctx.ListFields():
                return list(ctx.mentionedJID)
    return []


# ---------------------------------------------------------------------------
# Reply helper
# ---------------------------------------------------------------------------

def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def _cmd_add_subgroup(client, chat_jid, body: str, mentioned_jids: list[str]) -> None:
    """!add-subgroup <name> | @user1 @user2 …"""
    parts = body.split("|", 1)
    raw_name = parts[0].strip().lstrip("@")

    if not raw_name:
        _reply(client, chat_jid, "⚠️ Usage: `!add-subgroup <name> | @user1 @user2 …`")
        return

    name = raw_name.lower()

    if not _NAME_RE.match(name):
        _reply(
            client, chat_jid,
            "⚠️ Subgroup name must be 2-32 characters: letters, digits, hyphens, "
            "or underscores, starting with a letter.",
        )
        return

    if not mentioned_jids:
        _reply(client, chat_jid, "⚠️ Mention at least one user after the `|`.")
        return

    subgroups = _read_subgroups()
    existing = set(subgroups.get(name, []))
    added = [j for j in mentioned_jids if j not in existing]
    subgroups[name] = list(existing | set(mentioned_jids))
    _write_subgroups(subgroups)

    total = len(subgroups[name])
    if added:
        _reply(
            client, chat_jid,
            f"✅ Added {len(added)} member(s) to *@{name}* (total: {total}).",
        )
    else:
        _reply(client, chat_jid, f"ℹ️ All mentioned users are already in *@{name}* ({total} members).")


def _cmd_remove_from_subgroup(client, chat_jid, body: str, mentioned_jids: list[str]) -> None:
    """!remove-from-subgroup <name> | @user1 @user2 …"""
    parts = body.split("|", 1)
    raw_name = parts[0].strip().lstrip("@")
    name = raw_name.lower()

    if not name:
        _reply(client, chat_jid, "⚠️ Usage: `!remove-from-subgroup <name> | @user1 @user2 …`")
        return

    subgroups = _read_subgroups()
    if name not in subgroups:
        _reply(client, chat_jid, f"⚠️ Subgroup *@{name}* does not exist.")
        return

    if not mentioned_jids:
        _reply(client, chat_jid, "⚠️ Mention at least one user after the `|`.")
        return

    to_remove = set(mentioned_jids)
    remaining = [j for j in subgroups[name] if j not in to_remove]
    removed_count = len(subgroups[name]) - len(remaining)

    if remaining:
        subgroups[name] = remaining
        _write_subgroups(subgroups)
        _reply(
            client, chat_jid,
            f"✅ Removed {removed_count} member(s) from *@{name}* ({len(remaining)} remaining).",
        )
    else:
        # Last members removed — auto-delete the subgroup
        del subgroups[name]
        _write_subgroups(subgroups)
        _reply(
            client, chat_jid,
            f"🗑️ Subgroup *@{name}* deleted (no members remaining).",
        )


def _cmd_delete_subgroup(client, chat_jid, name_raw: str) -> None:
    """!delete-subgroup <name>"""
    name = name_raw.strip().lstrip("@").lower()

    if not name:
        _reply(client, chat_jid, "⚠️ Usage: `!delete-subgroup <name>`")
        return

    subgroups = _read_subgroups()
    if name not in subgroups:
        _reply(client, chat_jid, f"⚠️ Subgroup *@{name}* does not exist.")
        return

    del subgroups[name]
    _write_subgroups(subgroups)
    _reply(client, chat_jid, f"🗑️ Subgroup *@{name}* deleted.")


def _cmd_list_subgroups(client, chat_jid) -> None:
    """!list-subgroups"""
    subgroups = _read_subgroups()
    if not subgroups:
        _reply(client, chat_jid, "📭 No subgroups defined yet.")
        return

    lines = [f"• *@{name}* — {len(members)} member(s)" for name, members in sorted(subgroups.items())]
    _reply(client, chat_jid, f"*📋 Subgroups ({len(subgroups)})*\n\n" + "\n".join(lines))


def _cmd_subgroup_info(client, chat_jid, name_raw: str) -> None:
    """!subgroup-info <name>"""
    name = name_raw.strip().lstrip("@").lower()

    if not name:
        _reply(client, chat_jid, "⚠️ Usage: `!subgroup-info <name>`")
        return

    subgroups = _read_subgroups()
    if name not in subgroups:
        _reply(client, chat_jid, f"⚠️ Subgroup *@{name}* does not exist.")
        return

    members = subgroups[name]
    # Build @mentions so members render as tagged contacts (with names)
    mention_parts = [f"@{jid.split('@')[0]}" for jid in members]
    text = f"*@{name}* — {len(members)} member(s)\n\n" + "\n".join(f"  • {m}" for m in mention_parts)

    # Send as protobuf so mentionedJID makes WhatsApp resolve names
    proto_msg = Message(
        extendedTextMessage=ExtendedTextMessage(
            text=text,
            contextInfo=ContextInfo(
                mentionedJID=list(members),
            ),
        ),
    )
    try:
        client.send_message(chat_jid, proto_msg)
    except Exception as exc:
        log.error("Failed to send subgroup info: %s", exc)


# ---------------------------------------------------------------------------
# Subgroup @mention detection & tagging
# ---------------------------------------------------------------------------

def _detect_subgroup_mentions(text: str, subgroups: dict[str, list[str]]) -> list[str]:
    """Return subgroup names that are @mentioned in the text."""
    if not subgroups or "@" not in text:
        return []

    # Build a regex that matches any known subgroup name preceded by @
    # Sort by length (longest first) so "blog-team" matches before "blog"
    names = sorted(subgroups.keys(), key=len, reverse=True)
    pattern = r"@(" + "|".join(re.escape(n) for n in names) + r")(?:\b|$)"
    return [m.group(1) for m in re.finditer(pattern, text, re.IGNORECASE)]


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    # Clean blocked users (remove spaces, +, -, etc) so they match sender.User
    blocked_users: set[str] = {
        "".join(c for c in u if c.isdigit())
        for u in config.get("subgroup_blocked_users", set())
    }

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat

        # Only process group messages
        if getattr(chat, "Server", "") != "g.us":
            return

        # Ignore our own messages to prevent loops
        if message.Info.MessageSource.IsFromMe:
            return

        # Block listed users from using the subgroup feature
        sender = message.Info.MessageSource.Sender
        sender_user = getattr(sender, "User", "")
        if sender_user in blocked_users:
            return

        body = _get_text(message)
        if not body:
            return

        # ----- Command handling -----
        lower = body.lower()

        if lower == "!add-subgroup" or lower.startswith("!add-subgroup "):
            args = body[len("!add-subgroup"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!add-subgroup <name> | @user1 @user2 …`")
            else:
                _cmd_add_subgroup(client, chat, args, _get_mentioned_jids(message))
            return

        if lower == "!remove-from-subgroup" or lower.startswith("!remove-from-subgroup "):
            args = body[len("!remove-from-subgroup"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!remove-from-subgroup <name> | @user1 @user2 …`")
            else:
                _cmd_remove_from_subgroup(client, chat, args, _get_mentioned_jids(message))
            return

        if lower == "!delete-subgroup" or lower.startswith("!delete-subgroup "):
            args = body[len("!delete-subgroup"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!delete-subgroup <name>`")
            else:
                _cmd_delete_subgroup(client, chat, args)
            return

        if lower == "!list-subgroups":
            _cmd_list_subgroups(client, chat)
            return

        if lower == "!subgroup-info" or lower.startswith("!subgroup-info "):
            args = body[len("!subgroup-info"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!subgroup-info <name>`")
            else:
                _cmd_subgroup_info(client, chat, args)
            return

        # ----- @subgroup tag detection -----
        subgroups = _read_subgroups()
        matched = _detect_subgroup_mentions(body, subgroups)
        if not matched:
            return

        # Collect unique JIDs across all matched subgroups
        all_jids: list[str] = []
        seen: set[str] = set()
        tag_names: list[str] = []

        for name in matched:
            members = subgroups.get(name, [])
            if not members:
                continue
            tag_names.append(name)
            for jid in members:
                if jid not in seen:
                    seen.add(jid)
                    all_jids.append(jid)

        if not all_jids:
            return

        label = ", ".join(f"@{n}" for n in tag_names)
        text = f"📢 Tagging subgroup: {label}"

        proto_msg = Message(
            extendedTextMessage=ExtendedTextMessage(
                text=text,
                contextInfo=ContextInfo(
                    mentionedJID=all_jids,
                ),
            ),
        )

        try:
            client.send_message(chat, proto_msg)
            log.info(
                "Sent subgroup tag for %s — %d members mentioned.",
                label, len(all_jids),
            )
        except Exception as exc:
            log.error("Failed to send subgroup tag message: %s", exc)

    log.info("✅ Subgroups feature registered")
    return on_message
