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
        "`!add <text>` — add a new post and return its numeric ID.\n"
        "`!remove <id>` — remove a post from either list.\n"
        "`!to-do` or `!todo` — list posts that are not fully posted.\n"
        "`!posted <id> <stage>` — mark one stage complete.\n"
        "`!unposted <id> <stage>` — undo one completed stage.\n"
        "`!posted-list` — list posts with every stage complete.\n\n"
        "Stages: `design`, `instagram`/`insta`, `linkedin`, `twitter`.\n\n"
        "Examples:\n"
        "• `!add Publish the GSoC announcement`\n"
        "• `!remove 12`\n"
        "• `!to-do`\n"
        "• `!posted 12 instagram`\n"
        "• `!unposted 12 linkedin`\n"
        "• `!posted-list`"
    ),
    "cards": (
        "*🖼️ Card Generation*\n\n"
        "`!card <type> | <name> | <text>` — generate a PNG card.\n"
        "`!card-pdf <type> | <name> | <text>` — generate a PNG and editable PDF.\n\n"
        "Attach a profile photo to the command message. Types: `gsoc`, `lfx`,\n"
        "`hackathon`, `competitive`, `acm`, `internship`, `talk`, `custom`.\n"
        "You can also tag the bot and describe a card naturally; it will choose\n"
        "the closest template or use a controlled custom design.\n"
        "Examples:\n"
        "• `!card gsoc | Ananya Gupta | GSoC 2026 finalist`\n"
        "• `!card-pdf talk | Bibisha | Building with Python | Dev Workshop | https://example.com/logo.png`\n"
        "• `@bot create a sarcastic congratulations card for Zodiak for PBCTF 5.0; use https://example.com/logo.svg as the logo`"
    ),
    "community": (
        "*🏷️ Community Tagging*\n\n"
        "No command is required. Mention a WhatsApp community group in a\n"
        "community message and the bot silently notifies its members.\n\n"
        "Example: write `@community-name` in a group message."
    ),
    "subgroups": (
        "*🏷️ Custom Subgroups*\n\n"
        "`!add-subgroup <name> | @user1 @user2` — create a subgroup or add members (admin).\n"
        "`!remove-from-subgroup <name> | @user1 @user2` — remove members (admin).\n"
        "`!delete-subgroup <name>` — delete a subgroup (admin).\n"
        "`!list-subgroups` — list subgroup names and member counts (active user).\n"
        "`!subgroup-info <name>` — show subgroup members (active user).\n\n"
        "Names are 2–32 characters using letters, numbers, `-`, or `_`.\n"
        "Natural-language requests normalize names such as `2nd year` to `2nd-year`;\n"
        "existing close matches are reused unless you explicitly say `new` or `create`.\n"
        "Examples:\n"
        "• `!add-subgroup backend | @Ananya @Bibisha`\n"
        "• `!remove-from-subgroup backend | @Bibisha`\n"
        "• `!delete-subgroup backend`\n"
        "• `!list-subgroups`\n"
        "• `!subgroup-info backend`\n"
        "• Tag the group with `@backend` in any message."
    ),
    "admin": (
        "*🔐 User Administration*\n\n"
        "`!add-user [admin|member] @person` — create or update a user role (admin).\n"
        "`!remove-user @person` — deactivate a user (admin).\n"
        "`!users` — list active and inactive users (admin).\n"
        "`!admins`, `!admin-list`, or `!admins-list` — list active admins (active user).\n\n"
        "Mention the person; do not type their phone number manually.\n\n"
        "Examples:\n"
        "• `!add-user member @Ananya`\n"
        "• `!add-user admin @Bibisha`\n"
        "• `!remove-user @Ananya`\n"
        "• `!users`\n"
        "• `!admins` (aliases: `!admin-list`, `!admins-list`)"
    ),
    "events": (
        "*📋 Unified Work — Events, Tasks, Assignments, Progress*\n\n"
        "*Everyone*\n"
        "`!my` — show your assigned events and tasks. Example: `!my`\n"
        "`!work` — show the overall work view. Example: `!work pending`\n"
        "`!work event <id>` / `!work task <id>` — inspect one target. Example: `!work event 4`\n"
        "`!work status event <id>` — inspect assignment status. Example: `!work status event 4`\n"
        "`!work update event <id> <field> <value>` — record progress. Example: `!work update event 4 prs 3`\n"
        "`!work history event <id>` — show progress revisions. Example: `!work history event 4`\n"
        "`!work edit <revision_id> <new value>` — append a correction. Example: `!work edit 18 prs 4`\n"
        "`!work start event <id>` — mark your assignment in progress. Example: `!work start event 4`\n"
        "`!work complete task <id>` — mark your task completed. Example: `!work complete task 7`\n\n"
        "`!work reminders` — show reminder status. Example: `!work reminders`\n"
        "`!work reminders history [assignment_id]` — show reminder history. Example: `!work reminders history 22`\n\n"
        "*Admin only*\n"
        "`!work create event | <participation|organization> | <category> | <name> | [description]`\n"
        "`!work create task | <title> | [description text] | [due YYYY-MM-DD] | [priority low|medium|high]`\n"
        "`!work assign event <id> | @user` / `!work assign task <id> | @user`\n"
        "`!work unassign event <id> | @user` / `!work unassign task <id> | @user`\n"
        "`!work set-status event <id> [@user] <pending|in_progress|completed|cancelled>`\n\n"
        "`!work reminders config frequency 12 | window 09:00-18:00 | threshold 3 | channel @admin`\n"
        "`!work reminders run` — trigger an idempotent reminder run.\n\n"
        "Admin examples:\n"
        "• `!work create event | participation | gsoc | GSoC 2026 | Track applicants`\n"
        "• `!work create event | organization | workshop | Backend Workshop | Intro session`\n"
        "• `!work create task | Prepare report | Collect metrics | due 2026-08-01 | priority high`\n"
        "• `!work assign event 4 | @Ananya`\n"
        "• `!work assign task 7 | @Bibisha`\n"
        "• `!work unassign task 7 | @Bibisha`\n"
        "• `!work set-status event 4 @Ananya in_progress`\n"
        "• `!work reminders config frequency 12 | window 09:00-18:00 | threshold 3 | channel @admin`\n"
        "• `!work reminders run`\n\n"
        "Participation categories: `gsoc`, `lfx`, `hacktoberfest`, `research`, `other`.\n"
        "Organization categories: `recruitment`, `hackathon`, `workshop`, `bootcamp`, `other`.\n"
        "Progress statuses: `pending`, `in_progress`, `completed`, `cancelled`.\n"
        "When multiple users are assigned, an admin must mention the target user.\n"
        "Use spaces in references (`event 4`, `task 7`); old colon forms remain accepted for compatibility."
    ),
    "tasks": (
        "*✅ Tasks*\n\n"
        "*Admin commands:*\n"
        "`!add-task <title> [| description text] [| due YYYY-MM-DD] [| priority low|medium|high]` — create a task (admin).\n"
        "`!update-task <id> | field value` — edit `title`, `description`, `due`, `priority`, or lifecycle `status` (admin).\n"
        "`!delete-task <id>` — delete a task (admin).\n\n"
        "*Member commands:*\n"
        "`!tasks` — compatibility alias showing task assignments.\n"
        "`!task <id>` — show one assigned task.\n"
        "`!complete-task <id>` — mark your assigned task completed.\n\n"
        "Examples:\n"
        "• `!add-task Prepare report | due 2026-08-01 | priority high`\n"
        "• `!update-task 7 | priority high`\n"
        "• `!delete-task 7`\n"
        "• `!tasks`\n"
        "• `!task 7`\n"
        "• `!complete-task 7`"
    ),
    "updates": (
        "*📝 Assignment Updates*\n\n"
        "`!update <target> <field> <value>` — append a progress revision.\n"
        "`!update-edit <revision_id> <new value>` — append an edited revision.\n"
        "`!history <target>` — show the complete append-only history.\n"
        "`!status <target>` — show current progress and reminder status.\n"
        "`!set-status <target> <status>` — set progress status (admin).\n"
        "Targets: numeric assignment ID, `event <id>@<jid>`, or `task <id>@<jid>`.\n"
        "Statuses: `pending`, `in_progress`, `completed`, `cancelled`.\n"
        "Example: `!update task 7@919999999999@s.whatsapp.net note Waiting for review`\n"
        "Examples:\n"
        "• `!update task 7 note Waiting for review`\n"
        "• `!update-edit 18 Waiting for approval`\n"
        "• `!history task 7`\n"
        "• `!status event 4`\n"
        "• `!set-status event 4 in_progress`\n"
        "• `!help-update`"
    ),
    "incidents": (
        "*🚨 Incident Alerts*\n\n"
        "Receives Prometheus/Alertmanager-style webhook payloads and forwards alerts to the configured WhatsApp group. "
        "No chat commands required."
    ),
    "reminders": (
        "*⏰ Reminders*\n\n"
        "Canonical commands are under `!work reminders`; the older `!reminders` aliases remain supported.\n"
        "Members see only their own eligible assignments and history; admins see the overall system.\n\n"
        "Frequency and threshold must be positive numbers; times use 24-hour UTC.\n\n"
        "Examples:\n"
        "• `!work reminders`\n"
        "• `!work reminders history 22`\n"
        "• `!work reminders config frequency 12 | window 09:00-18:00 | threshold 3 | channel @admin`\n"
        "• `!work reminders run`\n"
        "Compatibility aliases: `!reminders`, `!reminder-config`, `!reminder-run`, `!reminder-history`."
    ),
    "labels": (
        "*🏷️ Labels*\n\n"
        "Labels group people, and a label can be assigned work in one command.\n"
        "Anyone may add or remove themselves; only admins may move other people or delete a label.\n"
        "A bare `!labels add <name>` means \"add me\".\n\n"
        "Examples:\n"
        "• `!labels`\n"
        "• `!labels add lfx-applicants`\n"
        "• `!labels of @Ananya`\n"
        "• `!labels create backend | @Ananya @Bibisha` _(admin)_\n"
        "• `!labels remove lfx-applicants`\n"
        "• `!labels delete backend` _(admin)_\n"
        "Then assign the whole label: `!work assign event 4 | @lfx-applicants`"
    ),
    "schema": (
        "*🧩 Event field schemas*\n\n"
        "Participation events define their own fields, and submitted values are checked against them. "
        "Events with no schema accept any field.\n"
        "Field types: `text`, `number`, `boolean`, `date`, `url`, `single_select`, `multi_select`, `list`.\n"
        "Select types carry options in brackets. Admin only, except viewing.\n\n"
        "Examples:\n"
        "• `!schema event 4`\n"
        "• `!schema create event 4 | orgs list | prs_opened number | accepted boolean`\n"
        "• `!schema create event 4 | org single_select(linkerd,istio)`\n"
        "• `!schema update event 4 | mentor text`\n"
        "• `!schema delete event 4 | mentor`\n"
        "• `!schema delete event 4` _(clears the whole schema)_"
    ),
    "reports": (
        "*📊 Reports and audit*\n\n"
        "Admin only. `!reports progress` shows every assignee's current field values as a table; "
        "long lists collapse to a count, and `!work history` has the full values.\n\n"
        "Examples:\n"
        "• `!reports`\n"
        "• `!reports progress event 4`\n"
        "• `!reports pending`\n"
        "• `!reports completed`\n"
        "• `!audit`\n"
        "• `!audit update` _(filter by operation)_"
    ),
}

# Work is the single documented workflow for event/task assignment and progress.
# Keep the older module names as compatibility aliases, but avoid presenting
# three competing command families in the top-level help.
MODULE_HELP["work"] = MODULE_HELP["events"]
MODULE_HELP["tasks"] = MODULE_HELP["work"]
MODULE_HELP["updates"] = MODULE_HELP["work"]

GLOBAL_HELP = (
    "*🤖 Bot Help*\n\n"
    "Use `!help <module>` for syntax, permissions, and examples.\n\n"
    "You can also tag the bot and describe an existing command naturally, "
    "for example: `@bot show my pending work`. The request is translated "
    "into a normal command and still uses the usual permissions.\n\n"
    "Examples: `!help`, `!help work`, `!help reminders`, `!help posted`.\n\n"
    "Available modules:\n"
    "• `!help media` — Task manager\n"
    "• `!help cards` — Card generation\n"
    "• `!help community` — Community group tags\n"
    "• `!help subgroups` — Custom subgroup tags\n"
    "• `!help admin` — User and role administration\n"
    "• `!help work` — Events, tasks, assignments, and progress\n"
    "• `!help labels` — Group people and assign work by label\n"
    "• `!help schema` — Event field definitions and validation\n"
    "• `!help reports` — Progress tables and audit log\n"
    "• `!help reminders` — Scheduled reminders\n"
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
                    "subgroup-info": "subgroups", "events": "work", "tasks": "work", "task": "work",
                    "assign": "work", "unassign": "work", "work": "work", "my": "work",
                    "update": "work", "edit": "work", "history": "work", "status": "work", "set-status": "work",
                    "start": "work", "complete": "work", "create": "work",
                    "create-event": "work", "delete-event": "work", "my-status": "work",
                    "add-user": "admin", "remove-user": "admin", "users": "admin",
                    "admins": "admin", "admin-list": "admin", "admins-list": "admin",
                    "add-task": "work", "complete-task": "work", "update-task": "work", "delete-task": "work",
                    "update-edit": "work", "help-update": "work",
                    "reminders": "reminders", "reminder-config": "reminders",
                    "reminder-run": "reminders", "reminder-history": "reminders"
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
