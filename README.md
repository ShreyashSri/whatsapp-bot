# WhatsApp Bot

A WhatsApp bot for [PBCTF](https://pbctf.pointblank.club) that posts registration stats to group chats — automatically every night and on demand via trigger word.

## Tech Stack

- [whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js) — WhatsApp Web automation
- [MongoDB](https://www.mongodb.com/) — reads user/team counts from the PBCTF database
- [node-cron](https://github.com/node-cron/node-cron) — schedules the nightly broadcast
- [PM2](https://pm2.keymetrics.io/) — keeps the bot running as a background process on the server

## Configuration

| Variable    | Description                        |
|-------------|------------------------------------|
| `MONGO_URI` | MongoDB connection string          |
| `GROUP_ID`  | WhatsApp group ID to post stats to |

Both are required. Set these in a `.env` file on the server (copy from `.env.example`). The `.env` is never committed or overwritten by CI.

To add more groups, append their IDs to the `GROUP_IDS` array in `bot.js`.

## Trigger Words

| Word     | Action                            |
|----------|-----------------------------------|
| `!stats` | Replies with current PBCTF stats  |

Type `!stats` (exact word) in any configured group to get an on-demand stats snapshot.

To add new triggers or actions, update `TRIGGER_WORDS` and the corresponding handler in the `message` event inside `bot.js`.

## Scheduled Broadcast

Stats are automatically sent to all configured groups every night at **midnight IST** (18:30 UTC).

## First-Time Setup (Server)

```bash
# 1. Create the directory
mkdir -p ~/whatsapp-bot && cd ~/whatsapp-bot

# 2. Create .env with your values (see .env.example)
nano .env

# 3. Install dependencies (after first deploy copies package files)
npm ci --omit=dev

# 4. Start with PM2
npx pm2 start bot.js --name whatsapp-bot
npx pm2 save

# 5. Scan the QR code printed in the logs to link your WhatsApp
npx pm2 logs whatsapp-bot
```

Once linked, the session is saved in `.wwebjs_auth/` and persists across restarts. The `.env` stays on the server permanently — CI never touches it.

## CI/CD (GitHub Actions)

Any push to the `main` branch automatically deploys to the server. No git setup needed on the server.

**How it works:**

1. Push code (or merge a PR) to `main`
2. GitHub Actions checks out the repo on the runner
3. Copies `bot.js`, `package.json`, `package-lock.json` to `~/whatsapp-bot` via SCP
4. SSHes in, runs `npm ci` to sync dependencies
5. Restarts the PM2 process (`whatsapp-bot`)

**Required GitHub Secrets** (set in repo → Settings → Secrets → Actions):

| Secret            | Description                                |
|-------------------|--------------------------------------------|
| `SSH_HOST`        | Server IP or hostname                      |
| `SSH_USER`        | SSH login username                         |
| `SSH_PRIVATE_KEY` | Private key for SSH auth (full PEM string) |
| `SSH_PORT`        | SSH port (optional, defaults to `22`)      |

> Deployment only happens when a PR is merged into `main` (or a direct push to `main`). Pushes to other branches are ignored.
