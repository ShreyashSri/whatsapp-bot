"""Media-team task manager feature.

Commands:
    !add <text>              — add a post to to-do
    !remove <id>             — remove a post (works on both lists)
    !to-do / !todo           — list pending posts
    !posted <id> <stage>     — mark a stage done
    !unposted <id> <stage>   — un-mark a stage
    !posted-list             — list fully posted entries
    !help [command]          — show help
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from neonize.events import MessageEv

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)


def _get_text(message: MessageEv) -> str:
    """Extract text body from a message (handles both plain, extended, and image captions)."""
    text = message.Message.conversation or ""
    if message.Message.extendedTextMessage and message.Message.extendedTextMessage.text:
        text = message.Message.extendedTextMessage.text
    elif message.Message.imageMessage and message.Message.imageMessage.caption:
        text = message.Message.imageMessage.caption
    return text.strip()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))

PLATFORMS = ["design", "instagram", "linkedin", "twitter"]
PLATFORM_ALIASES: dict[str, str] = {
    "design": "design", "d": "design", "des": "design",
    "insta": "instagram", "instagram": "instagram", "ig": "instagram",
    "linkedin": "linkedin", "li": "linkedin",
    "twitter": "twitter", "x": "twitter", "tw": "twitter",
}

# Lazily imported from cards once to avoid circular deps / heavy import at
# module level.
_card_types_str: str | None = None


def _get_card_types_str() -> str:
    global _card_types_str
    if _card_types_str is None:
        from cards import CARD_TYPES
        _card_types_str = ", ".join(CARD_TYPES)
    return _card_types_str

COMMAND_ALIASES: dict[str, str] = {"todo": "todo", "to-do": "todo", "card-pdf": "card"}

# ---------------------------------------------------------------------------
# State persistence (posts.json)
# ---------------------------------------------------------------------------

_POSTS_FILE: Path = Path.cwd() / "posts.json"


def _empty_platform_flags() -> dict[str, bool]:
    return {p: False for p in PLATFORMS}


def _normalize_entry_flags(entry: dict) -> dict:
    flags = _empty_platform_flags()
    platforms = entry.get("platforms", {})
    for p in PLATFORMS:
        if isinstance(platforms.get(p), bool):
            flags[p] = platforms[p]
    entry["platforms"] = flags
    return entry


def _read_posts() -> dict:
    if not _POSTS_FILE.exists():
        return {"nextId": 1, "todo": [], "posted": []}
    try:
        data = json.loads(_POSTS_FILE.read_text())
        todo = [_normalize_entry_flags(e) for e in data.get("todo", [])]
        posted = [_normalize_entry_flags(e) for e in data.get("posted", [])]
        return {"nextId": data.get("nextId", 1), "todo": todo, "posted": posted}
    except (json.JSONDecodeError, KeyError) as exc:
        log.error("posts.json corrupt, starting fresh: %s", exc)
        return {"nextId": 1, "todo": [], "posted": []}


def _write_posts(state: dict) -> None:
    _POSTS_FILE.write_text(json.dumps(state, indent=2))


def _normalize_platform(raw: str | None) -> str | None:
    if not raw:
        return None
    return PLATFORM_ALIASES.get(raw.lower())


def _platform_status_line(entry: dict) -> str:
    return " • ".join(
        f"{p.capitalize()}: {'✅' if entry['platforms'][p] else '⬜'}"
        for p in PLATFORMS
    )


def _format_todo_entry(entry: dict) -> str:
    return f"*#{entry['id']}* — {entry['text']}\n   {_platform_status_line(entry)}"


def _format_posted_entry(entry: dict) -> str:
    ts = entry.get("postedAt") or entry.get("createdAt", "")
    try:
        when = datetime.fromisoformat(ts).astimezone(IST).strftime("%b %d, %Y %I:%M %p")
    except (ValueError, TypeError):
        when = ts
    return f"*#{entry['id']}* — {entry['text']}\n   Posted: {when}"




# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------


def _reply(client: "NewClient", chat_jid, text: str) -> None:
    """Send a text reply to the given chat."""
    client.send_message(chat_jid, text)


async def _handle_media_command(client: "NewClient", message: MessageEv) -> None:
    """Process a single media-group command."""
    body = _get_text(message)
    if not body or not body.startswith("!"):
        return

    chat_jid = message.Info.MessageSource.Chat
    sender = str(message.Info.MessageSource.Sender)
    lower = body.lower()

    # --- !to-do / !todo ---
    if lower in ("!to-do", "!todo"):
        state = _read_posts()
        if not state["todo"]:
            _reply(client, chat_jid, "📭 To-do list is empty.")
            return
        lines = "\n\n".join(_format_todo_entry(e) for e in state["todo"])
        _reply(client, chat_jid, f"*📋 To-do ({len(state['todo'])})*\n\n{lines}")
        return

    # --- !posted-list ---
    if lower == "!posted-list":
        state = _read_posts()
        if not state["posted"]:
            _reply(client, chat_jid, "📭 No posts marked fully posted yet.")
            return
        lines = "\n\n".join(_format_posted_entry(e) for e in state["posted"])
        _reply(client, chat_jid, f"*✅ Posted ({len(state['posted'])})*\n\n{lines}")
        return

    # --- !add ---
    if lower == "!add" or lower.startswith("!add "):
        text = body[4:].strip()
        if not text:
            _reply(client, chat_jid, "⚠️ Usage: `!add <text>`")
            return
        state = _read_posts()
        entry = {
            "id": state["nextId"],
            "text": text,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "createdBy": sender,
            "platforms": _empty_platform_flags(),
        }
        state["todo"].append(entry)
        state["nextId"] += 1
        _write_posts(state)
        _reply(client, chat_jid, f"✅ Added *#{entry['id']}* — {entry['text']}")
        return

    # --- !remove ---
    if lower == "!remove" or lower.startswith("!remove "):
        id_str = body[7:].strip().lstrip("#")
        try:
            entry_id = int(id_str)
        except (ValueError, TypeError):
            _reply(client, chat_jid, "⚠️ Usage: `!remove <id>`")
            return

        state = _read_posts()
        todo_idx = next((i for i, e in enumerate(state["todo"]) if e["id"] == entry_id), -1)
        posted_idx = next((i for i, e in enumerate(state["posted"]) if e["id"] == entry_id), -1)

        if todo_idx != -1:
            removed = state["todo"].pop(todo_idx)
            where = "to-do"
        elif posted_idx != -1:
            removed = state["posted"].pop(posted_idx)
            where = "posted"
        else:
            _reply(client, chat_jid, f"❌ No entry with id *#{entry_id}*.")
            return

        _write_posts(state)
        _reply(client, chat_jid, f"🗑️ Removed *#{removed['id']}* from {where} — {removed['text']}")
        return

    # --- !posted <id> <stage> ---
    if lower == "!posted" or lower.startswith("!posted "):
        args = body[7:].strip().split()
        if len(args) < 2:
            _reply(client, chat_jid, "⚠️ Usage: `!posted <id> <stage>` (stage: design / insta / linkedin / twitter)")
            return

        try:
            entry_id = int(args[0].lstrip("#"))
        except ValueError:
            _reply(client, chat_jid, "⚠️ Id must be a number. Usage: `!posted <id> <stage>`")
            return

        platform = _normalize_platform(args[1])
        if not platform:
            _reply(
                client, chat_jid,
                f'⚠️ Unknown stage "{args[1]}". Use one of: design (d), instagram (insta / ig), '
                "linkedin (li), twitter (x / tw).",
            )
            return

        state = _read_posts()
        entry = next((e for e in state["todo"] if e["id"] == entry_id), None)
        if not entry:
            _reply(
                client, chat_jid,
                f"❌ No to-do entry with id *#{entry_id}*. (If it's already fully posted, check `!posted-list`.)",
            )
            return

        was_already = entry["platforms"][platform]
        entry["platforms"][platform] = True
        all_done = all(entry["platforms"][p] for p in PLATFORMS)

        if all_done:
            state["todo"] = [e for e in state["todo"] if e["id"] != entry_id]
            entry["postedAt"] = datetime.now(timezone.utc).isoformat()
            state["posted"].append(entry)

        _write_posts(state)

        header = (
            f"ℹ️ *#{entry_id}* was already marked on {platform}."
            if was_already
            else f"✅ *#{entry_id}* marked posted on {platform}."
        )
        footer = "\n\n🎉 All stages done — moved to posted." if all_done else ""
        _reply(client, chat_jid, f"{header}\n{_platform_status_line(entry)}{footer}")
        return

    # --- !unposted <id> <stage> ---
    if lower == "!unposted" or lower.startswith("!unposted "):
        args = body[9:].strip().split()
        if len(args) < 2:
            _reply(client, chat_jid, "⚠️ Usage: `!unposted <id> <stage>` (stage: design / insta / linkedin / twitter)")
            return

        try:
            entry_id = int(args[0].lstrip("#"))
        except ValueError:
            _reply(client, chat_jid, "⚠️ Id must be a number. Usage: `!unposted <id> <stage>`")
            return

        platform = _normalize_platform(args[1])
        if not platform:
            _reply(
                client, chat_jid,
                f'⚠️ Unknown stage "{args[1]}". Use one of: design (d), instagram (insta / ig), '
                "linkedin (li), twitter (x / tw).",
            )
            return

        state = _read_posts()
        entry = next((e for e in state["todo"] if e["id"] == entry_id), None)
        moved_back = False

        if not entry:
            posted_idx = next((i for i, e in enumerate(state["posted"]) if e["id"] == entry_id), -1)
            if posted_idx != -1:
                entry = state["posted"].pop(posted_idx)
                entry.pop("postedAt", None)
                state["todo"].append(entry)
                moved_back = True

        if not entry:
            _reply(client, chat_jid, f"❌ No entry with id *#{entry_id}*.")
            return

        was_marked = entry["platforms"][platform]
        entry["platforms"][platform] = False
        _write_posts(state)

        header = (
            f"↩️ *#{entry_id}* un-marked on {platform}."
            if was_marked
            else f"ℹ️ *#{entry_id}* was not marked on {platform}."
        )
        footer = "\n\n📋 Moved back to to-do." if moved_back else ""
        _reply(client, chat_jid, f"{header}\n{_platform_status_line(entry)}{footer}")
        return


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------


def register(client: "NewClient", config: dict) -> callable:
    """Register the media task-manager feature on the neonize client."""
    media_group_id = config.get("media_group_id")

    if not media_group_id:
        log.warning("MEDIA_GROUP_ID not set — skipping media task-manager feature.")
        return None

    def on_message(client: "NewClient", message: MessageEv):
        chat_obj = message.Info.MessageSource.Chat
        chat = f"{chat_obj.User}@{chat_obj.Server}"
        # Handle media-group commands
        if chat == media_group_id:
            try:
                import asyncio
                asyncio.run(_handle_media_command(client, message))
            except Exception as exc:
                log.error("Media command error: %s", exc)

    log.info("✅ Media task-manager feature registered")
    return on_message
