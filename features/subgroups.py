"""Custom Subgroups Feature.

Lets users create named subgroups (e.g. ``blogmaintainers``) and populate
them with WhatsApp users.  When someone writes ``@blogmaintainers`` in any
group the bot is in, every member of that subgroup receives a silent
notification (ghost mention) — their names do not appear in the message.

Subgroups are global (not per-group) and persisted in PostgreSQL.

Commands:
  !add-subgroup <name> | @user1 @user2 …
  !remove-from-subgroup <name> | @user1 @user2 …
  !delete-subgroup <name>
  !list-subgroups
  !subgroup-info <name>
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import (
    ContextInfo,
    ExtendedTextMessage,
    Message,
)

from db.subgroup_store import SubgroupStore

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

# Subgroup names: alphanumeric, hyphens, underscores (2-32 chars)
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{1,31}$")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _read_subgroups(store: SubgroupStore) -> dict[str, list[str]]:
    return store.read()


def _write_subgroups(store: SubgroupStore, data: dict[str, list[str]]) -> None:
    store.write(data)


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

def _cmd_add_subgroup(
    client, chat_jid, body: str, mentioned_jids: list[str], store: SubgroupStore
) -> None:
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

    subgroups = _read_subgroups(store)
    existing = set(subgroups.get(name, []))
    added = [j for j in mentioned_jids if j not in existing]
    subgroups[name] = list(existing | set(mentioned_jids))
    _write_subgroups(store, subgroups)

    total = len(subgroups[name])
    if added:
        _reply(
            client, chat_jid,
            f"✅ Added {len(added)} member(s) to *@{name}* (total: {total}).",
        )
    else:
        _reply(client, chat_jid, f"ℹ️ All mentioned users are already in *@{name}* ({total} members).")


def _cmd_remove_from_subgroup(
    client, chat_jid, body: str, mentioned_jids: list[str], store: SubgroupStore
) -> None:
    """!remove-from-subgroup <name> | @user1 @user2 …"""
    parts = body.split("|", 1)
    raw_name = parts[0].strip().lstrip("@")
    name = raw_name.lower()

    if not name:
        _reply(client, chat_jid, "⚠️ Usage: `!remove-from-subgroup <name> | @user1 @user2 …`")
        return

    subgroups = _read_subgroups(store)
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
        _write_subgroups(store, subgroups)
        _reply(
            client, chat_jid,
            f"✅ Removed {removed_count} member(s) from *@{name}* ({len(remaining)} remaining).",
        )
    else:
        # Last members removed — auto-delete the subgroup
        del subgroups[name]
        _write_subgroups(store, subgroups)
        _reply(
            client, chat_jid,
            f"🗑️ Subgroup *@{name}* deleted (no members remaining).",
        )


def _cmd_delete_subgroup(client, chat_jid, name_raw: str, store: SubgroupStore) -> None:
    """!delete-subgroup <name>"""
    name = name_raw.strip().lstrip("@").lower()

    if not name:
        _reply(client, chat_jid, "⚠️ Usage: `!delete-subgroup <name>`")
        return

    subgroups = _read_subgroups(store)
    if name not in subgroups:
        _reply(client, chat_jid, f"⚠️ Subgroup *@{name}* does not exist.")
        return

    del subgroups[name]
    _write_subgroups(store, subgroups)
    _reply(client, chat_jid, f"🗑️ Subgroup *@{name}* deleted.")


def _cmd_list_subgroups(client, chat_jid, store: SubgroupStore) -> None:
    """!list-subgroups"""
    subgroups = _read_subgroups(store)
    if not subgroups:
        _reply(client, chat_jid, "📭 No subgroups defined yet.")
        return

    lines = [f"• *@{name}* — {len(members)} member(s)" for name, members in sorted(subgroups.items())]
    _reply(client, chat_jid, f"*📋 Subgroups ({len(subgroups)})*\n\n" + "\n".join(lines))


def _cmd_subgroup_info(client, chat_jid, name_raw: str, store: SubgroupStore) -> None:
    """!subgroup-info <name>"""
    name = name_raw.strip().lstrip("@").lower()

    if not name:
        _reply(client, chat_jid, "⚠️ Usage: `!subgroup-info <name>`")
        return

    subgroups = _read_subgroups(store)
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


def _is_subgroup_command(text: str) -> bool:
    """Return whether text is an explicit subgroup command or @mention.

    Own-account commands are allowed for testing, but own-account replies and
    generated mention messages must not be fed back into the feature.
    """
    lower = text.lower()
    if any(
        lower == command or lower.startswith(f"{command} ")
        for command in (
            "!add-subgroup",
            "!remove-from-subgroup",
            "!delete-subgroup",
            "!list-subgroups",
            "!subgroup-info",
            "!labels",
        )
    ):
        return True
    if "@" in text:
        return True
    return False


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------

def register(client: "NewClient", config: dict) -> callable:
    # Clean blocked users (remove spaces, +, -, etc) so they match sender.User
    blocked_users: set[str] = {
        "".join(c for c in u if c.isdigit())
        for u in config.get("subgroup_blocked_users", set())
    }

    # Admin users for management commands (empty = no restriction)
    admin_users: set[str] = {
        "".join(c for c in u if c.isdigit())
        for u in config.get("admin_users", set())
    }

    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Subgroups feature requires db_session_factory")
    store = SubgroupStore(session_factory)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat

        # Only process group messages
        if getattr(chat, "Server", "") != "g.us":
            return

        # Block listed users from using the subgroup feature
        sender = message.Info.MessageSource.Sender
        sender_user = getattr(sender, "User", "")
        if sender_user in blocked_users:
            return

        body = _get_text(message)
        if not body:
            return

        # Allow explicit commands sent from the bot's own account for testing,
        # but ignore all other own-account messages. This prevents generated
        # subgroup replies/tags from triggering recursive loops.
        if message.Info.MessageSource.IsFromMe and not _is_subgroup_command(body):
            return

        # ----- Command handling -----
        lower = body.lower()

        # Admin-only gate for all management commands
        is_management_command = (
            lower.startswith("!add-subgroup")
            or lower.startswith("!remove-from-subgroup")
            or lower.startswith("!delete-subgroup")
            or lower == "!list-subgroups"
            or lower.startswith("!subgroup-info")
            or lower.startswith("!labels")
        )
        if is_management_command and admin_users and sender_user not in admin_users:
            _reply(client, chat, "⚠️ Only admins can use this command.")
            return

        if lower == "!add-subgroup" or lower.startswith("!add-subgroup "):
            args = body[len("!add-subgroup"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!add-subgroup <name> | @user1 @user2 …`")
            else:
                _cmd_add_subgroup(client, chat, args, _get_mentioned_jids(message), store)
            return

        if lower == "!remove-from-subgroup" or lower.startswith("!remove-from-subgroup "):
            args = body[len("!remove-from-subgroup"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!remove-from-subgroup <name> | @user1 @user2 …`")
            else:
                _cmd_remove_from_subgroup(client, chat, args, _get_mentioned_jids(message), store)
            return

        if lower == "!delete-subgroup" or lower.startswith("!delete-subgroup "):
            args = body[len("!delete-subgroup"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!delete-subgroup <name>`")
            else:
                _cmd_delete_subgroup(client, chat, args, store)
            return

        if lower == "!list-subgroups":
            _cmd_list_subgroups(client, chat, store)
            return

        if lower == "!subgroup-info" or lower.startswith("!subgroup-info "):
            args = body[len("!subgroup-info"):].strip()
            if not args:
                _reply(client, chat, "⚠️ Usage: `!subgroup-info <name>`")
            else:
                _cmd_subgroup_info(client, chat, args, store)
            return

        # ----- !labels aliases (route to subgroup handlers) -----
        if lower == "!labels" or lower.startswith("!labels "):
            args = body[7:].strip()
            if not args:
                _cmd_list_subgroups(client, chat, store)
                return

            parts = args.split(None, 1)
            subcmd = parts[0].lower()
            rest = parts[1].strip() if len(parts) > 1 else ""

            if subcmd in ("create", "add"):
                if not rest:
                    _reply(client, chat, "⚠️ Usage: `!labels create <name> | @user1 @user2 …`")
                else:
                    _cmd_add_subgroup(client, chat, rest, _get_mentioned_jids(message), store)
                return

            if subcmd == "remove":
                if not rest:
                    _reply(client, chat, "⚠️ Usage: `!labels remove <name> | @user1 @user2 …`")
                else:
                    _cmd_remove_from_subgroup(client, chat, rest, _get_mentioned_jids(message), store)
                return

            if subcmd == "delete":
                if not rest:
                    _reply(client, chat, "⚠️ Usage: `!labels delete <name>`")
                else:
                    _cmd_delete_subgroup(client, chat, rest, store)
                return

            if subcmd == "list":
                _cmd_list_subgroups(client, chat, store)
                return

            if subcmd == "info":
                if not rest:
                    _reply(client, chat, "⚠️ Usage: `!labels info <name>`")
                else:
                    _cmd_subgroup_info(client, chat, rest, store)
                return

            _reply(client, chat, f'⚠️ Unknown label subcommand "{subcmd}".\nUse: create, add, remove, delete, list, info')
            return

        # ----- @subgroup tag detection -----
        subgroups = _read_subgroups(store)
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
