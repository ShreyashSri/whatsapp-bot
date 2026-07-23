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

### 🎨 Card Generation

Generate achievement/congratulations cards as PNG images or editable PDFs.

| Command | Description |
|---------|-------------|
| `!card <type> \| <name> \| <text>` | Generate a PNG card (attach a photo) |
| `!card-pdf <type> \| <name> \| <text>` | Same, plus an editable PDF |

**Card types:** `gsoc`, `lfx`, `hackathon`, `competitive`, `acm`, `internship`, `custom`

Wrap any phrase in `[brackets]` to highlight it in the accent colour. Attach a profile photo to the same message.

**Examples:**
```
!card gsoc | Manas Hejmadi | For getting selected as mentor in [Google Summer of Code] 2026 with [API Dash]
!card-pdf lfx | Shubhang Sinha | For being a [LiFT Scholarship] holder for 2026
!card internship | Priya | Joining [Anthropic] as a Software Engineer Intern | https://example.com/anthropic.png
```

### 🚨 Incident Alerts

Receives Prometheus/Alertmanager-style webhook payloads and forwards alerts to a WhatsApp group.

- `POST /alert` endpoint on configurable port (default 8081)
- Deduplicates alerts — only sends on state changes (new incidents, resolved incidents)
- Persists state across restarts

## Tech Stack

- [neonize](https://github.com/krypton-byte/neonize) — WhatsApp Web automation (Python bindings for whatsmeow)
- [Playwright](https://playwright.dev/python/) — headless Chromium for card rendering
- [Flask](https://flask.palletsprojects.com/) — incident alert webhook server
- [PM2](https://pm2.keymetrics.io/) — process management on the server

## Configuration

| Variable | Description |
|----------|-------------|
| `GROUP_ID` | Primary WhatsApp group ID |
| `GROUP_IDS` | Optional comma-separated extra group IDs |
| `MEDIA_GROUP_ID` | WhatsApp group ID for media task manager |
| `INCIDENT_GROUP_ID` | WhatsApp group ID for incident alerts |
| `INCIDENT_PORT` | Webhook port (default: 8081) |

Set these in a `.env` file (copy from `.env.example`). The `.env` is never committed.

## Project Structure

```
whatsapp-bot/
├── bot.py                  # Main entry point
├── features/
│   ├── __init__.py
│   ├── media.py            # Media task manager
│   ├── cards.py            # Card generation
│   └── incidents.py        # Incident alerts
├── cards/
│   ├── __init__.py
│   ├── render.py           # HTML→PNG/PDF renderer
│   └── assets/
│       └── pb-logo.png
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

Once linked, the session is saved in `neonize.db` and persists across restarts.

### Running with PM2

```bash
pm2 start "python3 bot.py" --name whatsapp-bot
pm2 save
```

### Running with Docker

```bash
docker build -t whatsapp-bot .
docker run -d \
  --name whatsapp-bot \
  --env-file .env \
  -p 8081:8081 \
  -v $(pwd)/neonize.db:/app/neonize.db \
  -v $(pwd)/posts.json:/app/posts.json \
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
