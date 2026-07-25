#!/usr/bin/env python3
"""Standalone cron worker entrypoint for PBBot Reminders (PRD FR-7).

Can be executed via Linux crontab (e.g. `*/15 * * * * python /path/to/scripts/run_reminders_cron.py`)
or deployed as a Kubernetes CronJob.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ensure project root is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv()

from db.database import create_database
from db.auth import current_user
from db.reminder_store import ReminderStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cron_reminders")


def main() -> int:
    log.info("Starting cron reminder execution worker...")
    db_url = os.getenv("DATABASE_URL") or "sqlite:///pbbot.db"
    db = create_database(db_url)
    db.initialize()

    from db.auth import upsert_user
    system_user = upsert_user(db.session_factory, "system@s.whatsapp.net", role="admin", display_name="system")
    if not system_user:
        log.error("Failed to initialize system actor 'system@s.whatsapp.net'.")
        return 1

    reminder_store = ReminderStore(db.session_factory)
    
    # Optional force flag via CLI args
    force = "--force" in sys.argv or "-f" in sys.argv

    try:
        # Note: In standalone cron mode without active Neonize daemon client,
        # reminder_store will evaluate eligibility, record attempt logs,
        # and attempt dispatch through configured OpenWA/Neonize adapter.
        results = reminder_store.run_reminders(
            client=None,
            actor=system_user,
            force_ignore_window=force,
            source="cron",
        )
        log.info("Cron reminder execution finished successfully: %s", results)
        return 0
    except Exception as exc:
        log.exception("Error executing cron reminder run: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
