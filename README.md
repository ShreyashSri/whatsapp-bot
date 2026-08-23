# PBBot (WhatsApp Bot)

A fully-featured WhatsApp bot built with Python, [Neonize](https://github.com/krypton-byte/neonize), and PostgreSQL. It incorporates natural language processing (using Mistral/Gemini LLMs) to handle complex workloads including task management, media handling, alerts, group administration, and community engagement.

## Features & Bot Commands

PBBot boasts a wide array of modular functions that can be invoked via specific text commands or naturally by tagging the bot (e.g. `@bot show my pending work`). A comprehensive list of modules can be fetched in-chat via the `!help` command. 

Here is a breakdown of what the bot can perform:

###  Work, Task & Assignment Management
Automatically track, assign, and manage tasks and events across individuals or teams by conversing with the bot.
- **For Everyone:** 
  - **Check Workload:** Ask the bot to list your tasks. (e.g., `@bot what are my pending tasks?` or `@bot show my assigned work`).
  - **Log Progress:** Update the status of your assignments. (e.g., `@bot I've completed 3 PRs for event 4` or `@bot mark task 7 as done`).
- **For Admins:**
  - **Create Events & Tasks:** Initialize new workloads and assignments in plain English. (e.g., `@bot create a new participation event Hacktoberfest` or `@bot create a new task (taskname)`).
  - **Assign Work:** Delegate or remove work for specific members. (e.g., `@bot assign event 4 to @user` or `@bot remove @user from task 12`).
  - **Automated Reminders:** Schedule background jobs with customizable rules. (e.g., `@bot setup a reminder every 12 hours between 9 AM and 6 PM for admins`).

###  Natural Language Understanding
Leverages Mistral & Gemini models to understand and perform commands based on natural language instead of rigid command syntaxes. Mention the bot in a group chat and describe what you need (e.g., `@bot create a congratulations card for user2 for getting placed at xyz company`).

###  Media Task Manager & Card Generation
- **Media Posting Pipeline:** Track social media posts across various platforms (Instagram, LinkedIn, Twitter) by simply asking the bot. (e.g., `@bot add a new post about our upcoming workshop to the to-do list` or `@bot mark post 42 as posted on instagram`).
- **Custom Card Generation:** Automatically generate PNG and editable PDF cards (e.g. for Hackathons, Internships, Talks, and GSoC) by attaching an image and describing the details in plain English. (e.g., `@bot create a GSoC card for user3 saying she is a 2026 finalist`).

###  Community & Subgroups
Easily mention entire sub-communities to notify them simultaneously.
- **Community Tagging:** Simply tag a known community group (e.g., `@community-name`) in a participating group to alert all its members.
- **Custom Subgroups:** Admins can ask the bot to create dynamic subgroups. (e.g., `@bot create a subgroup called blog-team with @user1 and @user2`). Once created, simply tagging `@blog-team` in the chat will notify the relevant members.

###  User & Label Administration
Organize group members and manage bot permissions seamlessly.
- **Role Management:** Manage admin privileges by asking the bot (e.g., `@bot make @person an admin` or `@bot who are the current admins?`).
- **Work Labels:** Group users into labels for bulk assignment of tasks (e.g., `@bot create a label called lfx-applicants for @user1` and later `@bot assign event 4 to the @lfx-applicants label`).

###  Incident Alerts & Webhooks
PBBot receives Prometheus/Alertmanager-style webhook payloads on custom ports, converting them into WhatsApp alert messages, and automatically forwarding them to designated Incident or Fellowship alert groups.

## Project Structure

- `bot.py` - The primary application entry point. Initializes Neonize, the database, background workers, and handles WhatsApp connection state.
- `features/` - Contains modularized business logic and bot commands (e.g., `work.py`, `media.py`, `natural_language.py`, `incidents.py`, `admin.py`).
- `db/` - SQLAlchemy models, database setup, and schema migration logic.
- `cards/`, `scripts/` - Assorted helper modules, scripts, and media resources.
- `docker-compose.yml` - Docker compose configuration orchestrating the Python application alongside a PostgreSQL database.

## Prerequisites

- **Docker & Docker Compose** (Recommended for local setup)
- **Python 3.10+** (if running directly on the host machine)
- **PostgreSQL 16+**

## Configuration

Environment variables configure group IDs, LLM credentials, and database settings.

1. Copy the environment template:
   ```bash
   cp .env.example .env
   ```
2. Update `.env` with your specific configuration values:
   - Provide your `GROUP_ID`, `MEDIA_GROUP_ID`, etc. (Format: `<number>@g.us`).
   - Add your `MISTRAL_API_KEY` or `GEMINI_API_KEY` for AI/NL features.
   - Adjust PostgreSQL credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`).

## Running the Bot

The easiest way to run the bot is using Docker Compose, which automatically builds the bot container and provides a connected PostgreSQL database.

```bash
# Build and start services in the background
docker compose up -d --build

# View logs (useful for scanning the QR code for the initial WhatsApp web login)
docker compose logs -f bot
```

### Manual Setup

If you prefer to run it locally without Docker:

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Ensure you have a running PostgreSQL instance and configure `DATABASE_URL` in `.env`.
3. Start the bot:
   ```bash
   python bot.py
   ```
   *Upon the first start, Neonize will display a QR code in the terminal. Scan it with your WhatsApp app under "Linked Devices" to connect.*

## Initialization & Seeding

After the bot connects to your database for the first time, you may need to bootstrap admin privileges for yourself:

```bash
# Run this inside the container if using Docker, or on your host if running manually
python -m db.seed_admin <your-whatsapp-jid>
```

*(Note: Your JID format is typically `<country-code><phone-number>@s.whatsapp.net`)*
