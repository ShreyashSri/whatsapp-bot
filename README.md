# PBBot

PBBot is a modular WhatsApp bot built on the existing OpenWA gateway. The current runtime is intentionally vanilla: it verifies OpenWA webhooks, normalizes messages, and detects commands, but has no feature commands registered yet.

## Current flow

```text
OpenWA message.received webhook
        ↓
HMAC signature verification
        ↓
Normalized WhatsApp message
        ↓
Command parser
        ↓
Command registry
        ↓
Detected / handled outcome
```

This follows the same boundary used by `media_automata`: raw OpenWA payloads are normalized before orchestration. A future feature adds a command by registering a handler; it does not modify the OpenWA controller or parser.

## Run locally

```bash
cp .env.example .env
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn pbbot.api:app --host 0.0.0.0 --port 3000
```

Configure OpenWA to send `message.received` events to:

```text
POST http://<pbbot-host>:3000/webhooks/openwa
X-OpenWA-Signature: sha256=<hmac>
```

The detector accepts `/command` and `!command` forms. Since the registry is empty, detected commands return `registered: false` and execute nothing.

## Validation

```bash
ruff check .
pytest
```

## Documents

- [Product Requirements Document](PRD.md)
- [Team briefing PDF](PBBot-PRD-Brief.pdf)
- [PDF source](PBBot-PRD-Brief.html)
