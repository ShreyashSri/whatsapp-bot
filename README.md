# WhatsApp Bot

A WhatsApp bot for [PBCTF](https://pbctf.pointblank.club) that provides media task management, work/event/task tracking, reminders, achievement cards, community tagging, and incident/Fellowship alerting — built in Python with a pluggable feature architecture.

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

### 🗂️ Work Management

Track events, tasks, assignments, progress, labels, schemas, reports, and reminders in PostgreSQL.

| Command | Description |
|---------|-------------|
| `!work` | Show the overall work view |
| `!work create event ...` | Create an event (admin) |
| `!work create task ...` | Create a task (admin) |
| `!work assign ...` | Assign an event or task to a person or label |
| `!work update ...` | Record progress |
| `!work status ...` | Show assignment status |
| `!work reminders` | Show reminder status and history |
| `!reports` / `!audit` | View progress tables and audit history |

The compatibility commands (`!add-task`, `!update-task`, `!tasks`, `!update`, and similar aliases) remain supported.

#### Natural-language requests

Mention the bot with WhatsApp's native mention or use the local `@me` alias, then describe an existing command in ordinary language. For example:

```text
@me show my pending work
@me assign the website task to @Ananya
```

Natural-language requests are translated into normal commands and use the same permissions and group restrictions.

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
- The paired bot account is excluded from generated mentions
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
- State persisted in PostgreSQL; legacy JSON state is migrated when present

### 🚨 Incident Alerts

Receives Prometheus/Alertmanager-style webhook payloads and forwards alerts to the configured WhatsApp group.

- `POST /alert` endpoint on port 8081 by default
- Deduplicates alerts and sends only state changes
- Persists incident state in PostgreSQL across restarts

### 🤝 Fellowship Alerts

Receives Fellowship Tracker opportunity alerts and forwards them to the configured WhatsApp group.

- `POST /fellowship-alert` endpoint on port 8082 by default
- Requires the `X-Fellowship-Alert-Secret` header
- Deduplicates alerts using the supplied idempotency key

## Tech Stack

- [neonize](https://github.com/krypton-byte/neonize) — WhatsApp Web automation (Python bindings for whatsmeow)
- [PostgreSQL](https://www.postgresql.org/) + [SQLAlchemy](https://www.sqlalchemy.org/) — durable work, reminder, incident, and subgroup state
- [Playwright](https://playwright.dev/python/) — headless Chromium for card rendering
- [Flask](https://flask.palletsprojects.com/) — incident and Fellowship webhook servers
- Kubernetes + ArgoCD — production deployment
- PM2 — optional process management for a direct-host deployment

## Configuration

| Variable | Description |
|----------|-------------|
| `PBBOT_GROUP_ID` | Primary group allowed to trigger ordinary bot commands |
| `GROUP_ID` | Backwards-compatible primary group ID |
| `GROUP_IDS` | Optional comma-separated extra group IDs |
| `MEDIA_GROUP_ID` | Media task manager group ID |
| `REMINDER_GROUP_ID` | Team reminder group ID; ordinary commands remain restricted |
| `DATABASE_URL` | PostgreSQL connection URL |
| `MISTRAL_API_KEY` | Mistral key for natural-language command translation |
| `MISTRAL_MODEL` | Optional Mistral chat model override |
| `MISTRAL_CARD_MODEL` | Optional Mistral card model override |
| `GEMINI_API_KEY` | Gemini key used for natural-language fallback |
| `GEMINI_MODEL` | Optional Gemini model override |
| `NATURAL_LANGUAGE_KNOWLEDGE_URLS` | Optional comma-separated public URLs for explicit request context |
| `BOT_JID` | Optional bot JID; discovered automatically when omitted |
| `INCIDENT_GROUP_ID` | WhatsApp group for incident alerts |
| `INCIDENT_PORT` | Incident webhook port (default: 8081) |
| `FELLOWSHIP_ALERT_GROUP_ID` | WhatsApp group for Fellowship alerts |
| `FELLOWSHIP_ALERT_SECRET` | Secret sent in the Fellowship webhook header |
| `FELLOWSHIP_ALERT_PORT` | Fellowship webhook port (default: 8082) |
| `SUBGROUP_BLOCKED_USERS` | Optional comma-separated phone numbers blocked from subgroup operations |

Set these in a `.env` file (copy from `.env.example`). The `.env` is never committed.

The bot accepts ordinary commands only in the configured primary/media groups. Direct messages are blocked except for replies to tracked bot reminders. The paired bot account also ignores its own messages.

## Project Structure

```
whatsapp-bot/
├── bot.py                         # Main entry point and runtime wiring
├── features/
│   ├── media.py                   # Media task manager
│   ├── work.py                    # Events, tasks, assignments, progress
│   ├── natural_language.py        # Natural-language command translation
│   ├── reminders.py               # Scheduled reminders
│   ├── community_tag.py           # Community group tagging
│   ├── subgroups.py               # Custom subgroup tags
│   ├── incidents.py               # Incident alerts
│   └── fellowship_alerts.py       # Fellowship webhook alerts
├── db/                            # PostgreSQL models, stores, migrations
├── cards/                         # HTML→PNG/PDF card rendering
├── updates/                       # Assignment update compatibility layer
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── .github/workflows/
    ├── deploy.yml                # Build, push, and update infra
    └── diagnose.yml              # Manual direct-host diagnostics
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

Once linked, the session is saved at `NEONIZE_SESSION_DB` (the Docker Compose default is `/app/data/neonize.db`) and persists across restarts. Keep this file on persistent storage when moving between hosts or deployment environments.

### Running with PM2

```bash
pm2 start "python3 bot.py" --name whatsapp-bot
pm2 save
```

### Running with Docker

For the local PostgreSQL and bot stack:

```bash
docker compose up -d --build
```

To run only the bot image, keep the Neonize session on a persistent volume:

```bash
docker build -t whatsapp-bot .
docker run -d \
  --name whatsapp-bot \
  --env-file .env \
  -e NEONIZE_SESSION_DB=/app/data/neonize.db \
  -p 8081:8081 \
  -p 8082:8082 \
  -v "$(pwd)/data:/app/data" \
  whatsapp-bot
```

## CI/CD (GitHub Actions)

A push to `main` runs `.github/workflows/deploy.yml` and:

1. Builds and pushes `shreyashsri/whatsapp-bot:prod` and an immutable `prod-<short-sha>` image tag to Docker Hub.
2. Updates `argocd/whatsapp-bot/statefulset.yaml` in the `infra` repository to the immutable image tag.
3. Pushes the infra commit so ArgoCD can sync the production deployment.

The workflow expects these GitHub Secrets:

| Secret | Description |
|--------|-------------|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |
| `INFRA_PAT` | Token with permission to update the `pointblank-club/infra` repository |

The manual `diagnose.yml` workflow uses the optional SSH secrets below for the legacy direct-host deployment:

| Secret | Description |
|--------|-------------|
| `SSH_HOST` | Server IP or hostname |
| `SSH_USER` | SSH login username |
| `SSH_PRIVATE_KEY` | Private key for SSH auth (full PEM string) |
| `SSH_PORT` | SSH port (optional, defaults to `22`) |

The production bot uses the Kubernetes StatefulSet and persistent storage defined in the `infra` repository. Do not run the direct-host bot and the Kubernetes bot with the same WhatsApp session at the same time.
