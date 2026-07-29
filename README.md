# WhatsApp Bot

A WhatsApp bot for [PBCTF](https://pbctf.pointblank.club) that provides a media task manager, achievement card generator, and incident alerting — built in Python with a pluggable feature architecture.

## Features

### 📋 Media Task Manager

Track social media posts across platforms with a full to-do workflow.

| Command | Description |
|---------|-------------|
| `!add <text>` | Add a post to to-do |
| `!remove <id>` | Remove a post (works on both lists) |
| `!to-do` | List pending posts with stage checkboxes |
| `!posted <id> <stage>` | Mark a stage done (design / insta / linkedin / twitter) |
| `!unposted <id> <stage>` | Un-mark a stage |
| `!posted-list` | List fully posted entries |
| `!help [command]` | Show help or details for one command |

**Stages:** design • instagram • linkedin • twitter

When all four stages are marked, the entry auto-moves to the posted list.

Examples:

```text
!add Publish the GSoC announcement
!to-do
!posted 12 instagram
!unposted 12 linkedin
!posted-list
```

### 🎨 Card Generation

Generate achievement/congratulations cards as PNG images or editable PDFs.

| Command | Description |
|---------|-------------|
| `!card <type> \| <name> \| <text>` | Generate a PNG card (attach a photo) |
| `!card-pdf <type> \| <name> \| <text>` | Same, plus an editable PDF |

**Card types:** `gsoc`, `lfx`, `hackathon`, `competitive`, `acm`, `internship`, `talk`, `custom`

Wrap any phrase in `[brackets]` to highlight it in the accent colour. Attach a profile photo to the same message.

#### Talk Cards

Use `talk` for speaker thank-you posts:

```
!card talk | <speaker name> | <talk topic/title> | <event name> | <logo URL 1> | <logo URL 2>
```

The first three fields after `talk` are required: speaker name, talk topic/title, and event name.
The template writes the thank-you copy automatically: `THANK YOU !!`, `for representing us at`, and `and giving an insightful talk on`.
Logo URLs are optional. If you pass one logo, it is centered at the bottom; if you pass two, they are shown side by side. Use direct image URLs (`.png`, `.jpg`, `.webp`, `.svg`) for best results.

**Examples:**
```
!card gsoc | Manas Hejmadi | For getting selected as mentor in [Google Summer of Code] 2026 with [API Dash]
!card-pdf lfx | Shubhang Sinha | For being a [LiFT Scholarship] holder for 2026
!card internship | Priya | Joining [Anthropic] as a Software Engineer Intern | https://example.com/anthropic.png
!card talk | Dhruv Puri | Why Your Cluster-Wide Policies Are a Risk (And What to Do About It) | KubeCon + CloudNativeCon India 2026 | https://example.com/cncf.png | https://example.com/kubecon.png
```

### 🏷️ Community Group Tagging

When someone @mentions a group in any community chat, the bot silently pings every member of that group. Members receive a notification but the message only shows the group name — no individual @names are displayed.

- Works for any group the bot has joined within the community
- No configuration needed — auto-detects group mentions via WhatsApp's native `groupMentions` protocol
- The bot must be a member of the mentioned group to tag its participants

### 🏷️ Custom Subgroups

Create named subgroups of users that can be @mentioned in any group. Members get a silent notification — no names shown in the message.

| Command | Description |
|---------|-------------|
| `!add-subgroup <name> \| @user1 @user2 …` | Create subgroup or add members to an existing one |
| `!remove-from-subgroup <name> \| @user1 @user2 …` | Remove members (auto-deletes subgroup if empty) |
| `!delete-subgroup <name>` | Delete an entire subgroup |
| `!list-subgroups` | List all subgroups with member counts |
| `!subgroup-info <name>` | Show members of a specific subgroup |

To tag a subgroup, just write `@subgroupname` anywhere in a message — the bot detects it and pings all members.

- Subgroups are **global** — created once, usable in any group
- Names must be 2-32 characters: letters, digits, hyphens, or underscores
- A subgroup can never be empty; removing the last member auto-deletes it
- State persisted in PostgreSQL

Examples:

```text
!add-subgroup backend | @Ananya @Bibisha
!remove-from-subgroup backend | @Bibisha
!list-subgroups
!subgroup-info backend
!delete-subgroup backend
```

### 🚨 Incident Alerts

Receives Prometheus/Alertmanager-style webhook payloads and forwards alerts to a WhatsApp group.

- `POST /alert` endpoint on configurable port (default 8081)
- Deduplicates alerts — only sends on state changes (new incidents, resolved incidents)
- Persists state across restarts

### 🔐 Roles and authorization

Users are keyed by normalized WhatsApp JID and have one role: `admin` or `member`.
Seed the first administrator after PostgreSQL is available:

```bash
python -m db.seed_admin 919999999999@s.whatsapp.net
```

Admins can use `!add-user [admin|member] @person`, `!remove-user @person`, and `!users`. Subgroup changes require an admin; subgroup listing, info, and tagging require an active user. Removing or demoting the last active administrator is refused.

Examples:

```text
!add-user member @Ananya
!add-user admin @Bibisha
!remove-user @Ananya
!users
!admins
```

### 📌 Unified work and progress

Events and tasks share one assignment system. Use `!my` for your complete
workload and `!work` for detailed member or admin overviews. Filters include
`pending`, `event <id>`, and `task <id>`. Use `!work update`, `!work edit`,
`!work history`, `!work status`, and admin-only `!work set-status`; valid
progress statuses are `pending`, `in_progress`, `completed`, and `cancelled`.
Create events with a type and category:

```text
!work create event | <participation|organization> | <category> | <name> | [description]
```

Participation categories: `gsoc`, `lfx`, `hacktoberfest`, `research`, `other`.
Organization categories: `recruitment`, `hackathon`, `workshop`, `bootcamp`, `other`.

Create also accepts `start`, `end`, and `labels` fields, and `!update-event`
edits an existing event:

```text
!work create event | participation | lfx | LFX Term 3 2026 | Apps | start 2026-01-01 | end 2026-06-01 | labels ml,backend
!update-event 4 | name LFX Term 3 2026 (Revised) | end 2026-07-01
```

Assign several people at once, or a whole label:

```text
!work assign event 4 | @Ananya @Bibisha
!work assign event 4 | @third-years
```

### 🧩 Event field schemas

Participation events define their own fields, and submitted values are validated
against them. Events with no schema stay free-form.

| Command | Description |
|---------|-------------|
| `!schema event <id>` | Show an event's fields |
| `!schema create event <id> \| <name> <type> \| …` | Define the schema (replaces it) |
| `!schema update event <id> \| <name> <type>` | Add or retype one field |
| `!schema delete event <id> [\| <name>]` | Delete one field, or the whole schema |

**Field types:** `text` • `number` • `boolean` • `date` • `url` • `single_select` • `multi_select` • `list`

Select types carry their options in brackets. Values are canonicalised, so
`LINKERD` records as `linkerd`.

```text
!schema create event 4 | org single_select(linkerd,istio) | prs number | accepted boolean | proposal url
!work update event 4 prs 3
!work update event 4 prs banana     → rejected: expected a number
```

### 📊 Reports and audit

Admin-only. `!reports progress` renders the whole cohort as a table with the
current value of every field.

| Command | Description |
|---------|-------------|
| `!reports` | Counts across events, tasks, and assignments |
| `!reports progress event <id>` | Cohort table of current field values |
| `!reports pending` / `!reports completed` | Assignments by progress status |
| `!audit [<operation>]` | Recent operations with actor attribution |

### 🏷️ User labels

Labels are stored as subgroups, so a label is also mentionable.

| Command | Description |
|---------|-------------|
| `!labels` | List labels with member counts |
| `!labels of @user` | Show one user's labels |
| `!labels create <name> \| @user …` | Create a label or add members |
| `!labels remove <name> \| @user …` | Remove members (deletes the label when empty) |
| `!labels delete <name>` | Delete a label |

Reminder controls are unified under `!work`:

```text
!work reminders
!work reminders history [assignment_id]
!work reminders config frequency 12 | window 09:00-18:00 | threshold 3 | channel @admin
!work reminders run
```

Members see only their own reminder status/history. The older `!reminders`,
`!reminder-config`, `!reminder-run`, and `!reminder-history` forms remain
compatibility aliases.

Create tasks with:

```text
!work create task | <title> | [description text] | [due YYYY-MM-DD] | [priority low|medium|high]
```

Work examples:

```text
!my
!work pending
!work event 4
!work update event 4 prs 3
!work history event 4
!work start event 4
!work complete task 7
!work assign task 7 | @Bibisha
!work set-status event 4 @Ananya in_progress
```

Card and help examples:

```text
!card gsoc | Ananya Gupta | GSoC 2026 finalist
!card-pdf talk | Bibisha | Building with Python | Dev Workshop | https://example.com/logo.png
!help
!help work
!help reminders
```

The older progress, task, event, and assignment commands are routed through
the same work handler for compatibility; `!events` and `!tasks` remain view
aliases for the unified system.

## Tech Stack

- [neonize](https://github.com/krypton-byte/neonize) — WhatsApp Web automation (Python bindings for whatsmeow)
- [SQLAlchemy](https://www.sqlalchemy.org/) + [psycopg](https://www.psycopg.org/) — PostgreSQL persistence for bot state
- [Playwright](https://playwright.dev/python/) — headless Chromium for card rendering
- [Flask](https://flask.palletsprojects.com/) — incident alert webhook server
- [PM2](https://pm2.keymetrics.io/) — process management on the server

## Configuration

| Variable | Description |
|----------|-------------|
| `GROUP_ID` | Primary WhatsApp group ID |
| `GROUP_IDS` | Optional comma-separated extra group IDs |
| `PBBOT_GROUP_ID` | WhatsApp group ID where inbound bot commands are allowed (defaults to `GROUP_ID`) |
| `DATABASE_URL` | PostgreSQL connection URL |
| `MEDIA_GROUP_ID` | WhatsApp group ID for media task manager |
| `INCIDENT_GROUP_ID` | WhatsApp group ID for incident alerts |
| `INCIDENT_PORT` | Webhook port (default: 8081) |
| `SUBGROUP_BLOCKED_USERS` | Comma-separated phone numbers blocked from using subgroups |

Set these in a `.env` file (copy from `.env.example`). The `.env` is never committed.

## Project Structure

```
whatsapp-bot/
├── bot.py                  # Main entry point
├── features/
│   ├── __init__.py
│   ├── media.py            # Media task manager
│   ├── cards.py            # Card generation
│   ├── community_tag.py    # Community group tagging
│   ├── subgroups.py        # Custom subgroups
│   └── incidents.py        # Incident alerts
├── cards/
│   ├── __init__.py
│   ├── render.py           # HTML→PNG/PDF renderer
│   └── assets/
│       └── pb-logo.png
├── db/                      # PostgreSQL setup, models, stores, and migration
├── requirements.txt
├── Dockerfile
├── .env.example
└── .github/workflows/
    ├── deploy.yml
    └── diagnose.yml
```

## Adding New Features

1. Create `features/your_feature.py`
2. Implement a `register(client, config)` function that hooks into the neonize client
3. Import and call it in `bot.py`:

```python
from features.your_feature import register as register_your_feature
register_your_feature(client, config)
```

That's it. The feature system is fully pluggable — no framework, no boilerplate.

## First-Time Setup

```bash
# 1. Clone and enter the directory
cd whatsapp-bot

# 2. Create .env with your values
cp .env.example .env
nano .env

# 3. Create a virtual environment and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Install Playwright's Chromium (for card rendering)
playwright install --with-deps chromium

# 5. Start the bot
python bot.py

# 6. Scan the QR code printed in the terminal to link WhatsApp
```

On the first start, existing `posts.json`, `subgroups.json`, and
`incident_state.json` files are imported if their PostgreSQL tables are empty.
The JSON files are left untouched as backups; normal operation uses PostgreSQL.
The Neonize session is saved separately in `neonize.db` and persists across
restarts.

### Running with PM2

```bash
pm2 start "python3 bot.py" --name whatsapp-bot
pm2 save
```

### Running with Docker

The repository includes a Compose setup with PostgreSQL and the bot. Copy
`.env.example` to `.env`, set the WhatsApp group values and a PostgreSQL
password, then run:

```bash
docker compose up --build
```

PostgreSQL data is stored in the `postgres-data` volume and the Neonize
WhatsApp session is stored separately in the `neonize-data` volume. Scan the
QR code shown in the bot logs on first startup.

To stop the services while retaining data:

```bash
docker compose down
```

Do not use `docker compose down -v` unless you intentionally want to delete
the PostgreSQL and WhatsApp session volumes.

For a manually managed PostgreSQL instance, set `DATABASE_URL` directly and
run the bot container without the Compose PostgreSQL service.

```bash
docker build -t whatsapp-bot .
docker run -d \
  --name whatsapp-bot \
  --env-file .env \
  -p 8081:8081 \
  -v whatsapp-neonize-data:/app/data \
  whatsapp-bot
```

## CI/CD (GitHub Actions)

Any push to `main` automatically:
1. Builds and pushes the Docker image to `shreyashsri/whatsapp-bot` (tagged `latest` + commit SHA)
2. Deploys to the server via SCP + SSH
3. Installs dependencies and restarts the PM2 process

**Required GitHub Secrets:**

| Secret | Description |
|--------|-------------|
| `SSH_HOST` | Server IP or hostname |
| `SSH_USER` | SSH login username |
| `SSH_PRIVATE_KEY` | Private key for SSH auth (full PEM string) |
| `SSH_PORT` | SSH port (optional, defaults to `22`) |
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |

> Deployment only happens when a PR is merged into `main` (or a direct push to `main`).
