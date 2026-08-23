"""Global Help Feature.

Provides a unified `!help` command that works globally.
- `!help` lists all features.
- `!help <topic>` shows NLP examples for that feature.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from neonize.events import MessageEv
from features.subgroups import _get_text
from features.text import public_text

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

def _reply(client: "NewClient", chat_jid, text: str) -> None:
    client.send_message(chat_jid, text)

# Module-level help definitions
MODULE_HELP = {
    "work": (
        "*📋 Work, Task & Assignment Management*\n\n"
        "Automatically track, assign, and manage tasks and events by conversing with the bot.\n\n"
        "*For Everyone:*\n"
        "• `@bot what are my pending tasks?`\n"
        "• `@bot show my assigned work`\n"
        "• `@bot I've completed 3 PRs for event 4`\n"
        "• `@bot mark task 7 as done`\n"
        "• `@bot show the progress for event 4`\n"
        "• `@bot show event 4 history`\n\n"
        "*For Admins:*\n"
        "• `@bot create a new participation event Hacktoberfest`\n"
        "• `@bot create a new task Prepare report due August 1st`\n"
        "• `@bot assign event 4 to @user1`\n"
        "• `@bot remove @user2 from task 12`\n"
        "• `@bot mark event 4 as completed for @user1`\n"
        "• `@bot setup a reminder every 12 hours between 9 AM and 6 PM for admins`\n"
        "• `@bot show the progress report for event 4`"
    ),
    "media": (
        "*📱 Media Task Manager*\n\n"
        "Track social media posts across Instagram, LinkedIn, and Twitter by asking the bot.\n\n"
        "Examples:\n"
        "• `@bot add a new post about our upcoming workshop to the to-do list`\n"
        "• `@bot show pending posts`\n"
        "• `@bot mark post 42 as posted on instagram`\n"
        "• `@bot undo post 42 on instagram`\n"
        "• `@bot remove post 12`\n"
        "• `@bot show all completed posts`"
    ),
    "cards": (
        "*🖼️ Custom Card Generation*\n\n"
        "Generate PNG and editable PDF cards (GSoC, LFX, Hackathon, Internship, Talk, etc.) "
        "by attaching a photo and describing the details in plain English.\n\n"
        "Examples:\n"
        "• `@bot create a GSoC card for @user1 saying she is a 2026 finalist` _(attach photo)_\n"
        "• `@bot generate an internship card for @user2 at Google` _(attach photo)_\n"
        "• `@bot create a talk card for @user3 — talk: Building with Python, event: Dev Workshop`\n"
        "• `@bot make a sarcastic congratulations card for @user3 for winning PBCTF 5.0`\n"
        "• `@bot create a card PDF for the LFX workshop talk; use https://example.com/logo.png as the logo`"
    ),
    "community": (
        "*📢 Community Tagging*\n\n"
        "Mention a WhatsApp community group in any message to silently notify all its members.\n\n"
        "Example: write `@community-name` in a group message — no bot mention needed."
    ),
    "subgroups": (
        "*🏷️ Custom Subgroups*\n\n"
        "Create named subgroups and tag all their members with a single @mention.\n\n"
        "*Admins:*\n"
        "• `@bot create a subgroup called blog-team with @user1 and @user2`\n"
        "• `@bot add @user3 to the blog-team subgroup`\n"
        "• `@bot remove @user2 from blog-team`\n"
        "• `@bot delete the blog-team subgroup`\n\n"
        "*Everyone:*\n"
        "• `@bot list all subgroups`\n"
        "• `@bot who is in the blog-team subgroup?`\n"
        "• Write `@blog-team` in any message to notify all its members."
    ),
    "admin": (
        "*🔐 User & Role Administration*\n\n"
        "Manage bot access and admin privileges by asking the bot.\n\n"
        "Examples:\n"
        "• `@bot make @user1 an admin`\n"
        "• `@bot add @user2 as a member`\n"
        "• `@bot remove @user3 from the bot`\n"
        "• `@bot who are the current admins?`\n"
        "• `@bot list all users`"
    ),
    "labels": (
        "*🏷️ Work Labels*\n\n"
        "Group users into labels for bulk assignment of tasks and events.\n\n"
        "Examples:\n"
        "• `@bot create a label called lfx-applicants for @user1 and @user2`\n"
        "• `@bot add me to the lfx-applicants label`\n"
        "• `@bot remove @user3 from lfx-applicants`\n"
        "• `@bot assign event 4 to the lfx-applicants label`\n"
        "• `@bot show labels for @user1`\n"
        "• `@bot delete the lfx-applicants label`"
    ),
    "schema": (
        "*🧩 Event Field Schemas*\n\n"
        "Define custom fields for participation events to track structured progress.\n\n"
        "Examples:\n"
        "• `@bot show the schema for event 4`\n"
        "• `@bot add fields orgs, prs_opened, and accepted to event 4`\n"
        "• `@bot add a field org with options linkerd and istio to event 4`\n"
        "• `@bot remove the mentor field from event 4`\n"
        "• `@bot clear the schema for event 4`"
    ),
    "reports": (
        "*📊 Reports & Audit*\n\n"
        "Query progress tables and the audit log by asking the bot. Admin only.\n\n"
        "Examples:\n"
        "• `@bot show the overall work report`\n"
        "• `@bot show progress for event 4`\n"
        "• `@bot list all pending assignments`\n"
        "• `@bot list all in-progress assignments`\n"
        "• `@bot show the audit log`\n"
        "• `@bot show audit log for update operations`"
    ),
    "reminders": (
        "*⏰ Automated Reminders*\n\n"
        "Schedule background reminders with customizable rules by asking the bot.\n\n"
        "Examples:\n"
        "• `@bot show reminder status`\n"
        "• `@bot setup a reminder every 12 hours between 9 AM and 6 PM for admins`\n"
        "• `@bot run reminders now`\n"
        "• `@bot send a reminder for event 4`\n"
        "• `@bot show reminder history for assignment 22`"
    ),
    "incidents": (
        "*🚨 Incident Alerts*\n\n"
        "Receives Prometheus/Alertmanager-style webhook payloads and forwards alerts "
        "to the configured WhatsApp group. No interaction required."
    ),
}

# Aliases — all point to the same unified work help
MODULE_HELP["events"] = MODULE_HELP["work"]
MODULE_HELP["tasks"] = MODULE_HELP["work"]
MODULE_HELP["updates"] = MODULE_HELP["work"]

GLOBAL_HELP = (
    "*🤖 PBBot*\n\n"
    "Just @mention me in any group chat and describe what you need in plain English.\n\n"
    "Examples:\n"
    "• `@bot what are my pending tasks?`\n"
    "• `@bot assign event 4 to @user1`\n"
    "• `@bot create a GSoC card for @user2` _(attach photo)_\n"
    "• `@bot mark post 42 as posted on instagram`\n"
    "• `@bot show progress for event 4`\n\n"
    "Use `!help <topic>` for more examples:\n"
    "• `!help work` — events, tasks & assignments\n"
    "• `!help media` — social media post pipeline\n"
    "• `!help cards` — card generation\n"
    "• `!help subgroups` — custom group tags\n"
    "• `!help labels` — bulk assignment labels\n"
    "• `!help admin` — user & role management\n"
    "• `!help schema` — event field definitions\n"
    "• `!help reports` — progress & audit reports\n"
    "• `!help reminders` — automated reminders\n"
    "• `!help community` — community group tagging\n"
    "• `!help incidents` — incident alerts"
)


def register(client: "NewClient", config: dict) -> callable:
    def on_message(client: "NewClient", message: MessageEv):
        if not message.Info or not message.Info.MessageSource:
            return

        chat = message.Info.MessageSource.Chat

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
                    # media
                    "add": "media", "remove": "media", "to-do": "media", "todo": "media",
                    "posted": "media", "unposted": "media", "posted-list": "media",
                    # cards
                    "card": "cards", "card-pdf": "cards",
                    # subgroups
                    "add-subgroup": "subgroups", "remove-from-subgroup": "subgroups",
                    "delete-subgroup": "subgroups", "list-subgroups": "subgroups",
                    "subgroup-info": "subgroups",
                    # work / events / tasks
                    "events": "work", "tasks": "work", "task": "work",
                    "assign": "work", "unassign": "work", "undo": "work",
                    "work": "work", "my": "work",
                    "update": "work", "edit": "work", "history": "work",
                    "status": "work", "set-status": "work",
                    "start": "work", "complete": "work", "create": "work",
                    "create-event": "work", "update-event": "work", "delete-event": "work",
                    "add-task": "work", "complete-task": "work",
                    "update-task": "work", "delete-task": "work",
                    "update-edit": "work",
                    # admin
                    "add-user": "admin", "remove-user": "admin",
                    "users": "admin", "admins": "admin",
                    "admin-list": "admin", "admins-list": "admin",
                    # others
                    "schema": "schema",
                    "labels": "labels", "label": "labels",
                    "report": "reports", "reports": "reports", "audit": "reports",
                    "reminders": "reminders", "reminder-config": "reminders",
                    "reminder-run": "reminders", "reminder-history": "reminders",
                }

                module = cmd_to_module.get(args)
                if module:
                    _reply(client, chat, MODULE_HELP[module])
                else:
                    known = ", ".join(k for k in MODULE_HELP if k not in ("events", "tasks", "updates"))
                    _reply(client, chat, f"⚠️ Unknown topic '{public_text(args, limit=80)}'.\nTry: {known}")
            return

    log.info("✅ Help feature registered")
    return on_message
