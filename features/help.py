"""Global Help Feature.

Provides a unified `!help` command that works globally.
- `!help` lists all modules.
- `!help <module>` shows commands for that specific module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from neonize.events import MessageEv

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

def _get_text(message: MessageEv) -> str:
    text = message.Message.conversation or ""
    if message.Message.extendedTextMessage and message.Message.extendedTextMessage.text:
        text = message.Message.extendedTextMessage.text
    elif message.Message.imageMessage and message.Message.imageMessage.caption:
        text = message.Message.imageMessage.caption
    return text.strip()

def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)

# Module-level help definitions
MODULE_HELP = {
    "media": (
        "*📋 Media Task Manager*\n\n"
        "`!add <text>` — add a post to to-do\n"
        "`!remove <id>` — remove a post\n"
        "`!to-do` / `!todo` — list pending posts\n"
        "`!posted <id> <stage>` — mark a stage done\n"
        "`!unposted <id> <stage>` — un-mark a stage\n"
        "`!posted-list` — list fully posted entries\n\n"
        "_Stages:_ design • instagram • linkedin • twitter\n"
        "_Card types:_ gsoc, lfx, hackathon, competitive, acm, internship, talk, custom"
    ),
    "cards": (
        "*🖼️ Card Generation*\n\n"
        "`!card <type> | <name> | <text>` — generate a PNG card (attach photo)\n"
        "`!card-pdf <type> | <name> | <text>` — generate PNG + editable PDF\n\n"
        "See `!help media` for available card types."
    ),
    "community": (
        "*🏷️ Community Tagging*\n\n"
        "When someone @mentions a group in any community chat, the bot silently pings every member of that group. "
        "No commands required — it happens automatically."
    ),
    "subgroups": (
        "*🏷️ Custom Subgroups*\n\n"
        "`!add-subgroup <name> | @user1 ...` — create/add members\n"
        "`!remove-from-subgroup <name> | @user1 ...` — remove members\n"
        "`!delete-subgroup <name>` — delete entire subgroup\n"
        "`!list-subgroups` — list all subgroups\n"
        "`!subgroup-info <name>` — show members of a subgroup\n\n"
        "Subgroup changes require an admin; listing, info, and tagging require an active user.\n\n"
        "To tag a subgroup, just write `@subgroupname` anywhere in a message."
    ),
    "admin": (
        "*🔐 User Administration*\n\n"
        "`!add-user [admin|member] @person` — add or update a user\n"
        "`!remove-user @person` — deactivate a user\n"
        "`!users` — list users\n\n"
        "These commands require an active administrator."
    ),
    "events": (
        "*📋 Events*\n\n"
        "`!events` — list active events\n"
        "`!my` — show your assignments\n"
        "`!my-status <id> | <status>` — update your assignment\n"
        "`!create-event <type> | <name> | [description]` — create an event (admin)\n"
        "`!assign <id> | @user` / `!unassign <id> | @user` — manage assignments (admin)\n"
        "`!delete-event <id>` / `!set-status <id> | <status>` — manage events (admin)"
    ),
    "incidents": (
        "*🚨 Incident Alerts*\n\n"
        "Receives Prometheus/Alertmanager-style webhook payloads and forwards alerts to the configured WhatsApp group. "
        "No chat commands required."
    )
}

GLOBAL_HELP = (
    "*🤖 Bot Help*\n\n"
    "Available modules:\n"
    "• `!help media` — Task manager\n"
    "• `!help cards` — Card generation\n"
    "• `!help community` — Community group tags\n"
    "• `!help subgroups` — Custom subgroup tags\n"
    "• `!help admin` — User and role administration\n"
    "• `!help incidents` — Incident alerts\n\n"
    "Type `!help <module>` for detailed commands."
)

def register(client: "NewClient", config: dict) -> callable:
    def on_message(client: "NewClient", message: MessageEv):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat
        
        # Only process group messages (you can change this to allow DM if needed)
        if getattr(chat, "Server", "") != "g.us":
            return

        body = _get_text(message)
        if not body:
            return

        lower = body.lower()
        if lower == "!help":
            _reply(client, chat, GLOBAL_HELP)
            return

        if lower.startswith("!help "):
            args = lower[6:].strip()
            
            if args in MODULE_HELP:
                _reply(client, chat, MODULE_HELP[args])
            else:
                cmd_to_module = {
                    "add": "media", "remove": "media", "to-do": "media", "todo": "media",
                    "posted": "media", "unposted": "media", "posted-list": "media",
                    "card": "cards", "card-pdf": "cards",
                    "add-subgroup": "subgroups", "remove-from-subgroup": "subgroups",
                    "delete-subgroup": "subgroups", "list-subgroups": "subgroups",
                    "subgroup-info": "subgroups", "events": "events", "assign": "events",
                    "unassign": "events", "create-event": "events", "delete-event": "events",
                    "set-status": "events", "my": "events", "my-status": "events"
                }
                
                module = cmd_to_module.get(args)
                if module:
                    _reply(client, chat, MODULE_HELP[module])
                else:
                    known = ", ".join(MODULE_HELP.keys())
                    _reply(client, chat, f"⚠️ Unknown command '{args}'.\nAvailable options: {known}")
            return

    log.info("✅ Help feature registered")
    return on_message
