#!/usr/bin/env python3
"""Standalone cron worker entrypoint for PBBot Reminders (PRD FR-7).

Can be executed via Linux crontab (e.g. `*/15 * * * * python /path/to/scripts/run_reminders_cron.py`)
or deployed as a Kubernetes CronJob.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

# Ensure project root is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from neonize.client import NewClient  # noqa: E402
from neonize.events import ConnectedEv  # noqa: E402
from db.database import create_database  # noqa: E402
from db.reminder_store import ReminderStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cron_reminders")


def main() -> int:
    log.info("Starting cron reminder execution worker...")
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL is required for cron reminder execution.")
        return 2

    session_db = Path(os.getenv("NEONIZE_SESSION_DB", "neonize.db"))
    if not session_db.is_absolute():
        session_db = ROOT_DIR / session_db
    if not session_db.exists():
        log.error("Neonize session database not found: %s", session_db)
        return 2

    db = create_database(db_url)
    db.initialize()

    from db.auth import upsert_user
    system_user = upsert_user(db.session_factory, "system@s.whatsapp.net", role="admin", display_name="system")
    if not system_user:
        log.error("Failed to initialize system actor 'system@s.whatsapp.net'.")
        return 1

    reminder_store = ReminderStore(db.session_factory)
    force = "--force" in sys.argv or "-f" in sys.argv
    client = NewClient(str(session_db))
    result_code = {"value": 1}
    finished = threading.Event()

    @client.event(ConnectedEv)
    def on_connected(_client: NewClient, _event: ConnectedEv):
        def run_once():
            try:
                results = reminder_store.run_reminders(
                    client=client,
                    actor=system_user,
                    force_ignore_window=force,
                    source="cron",
                )
                log.info("Cron reminder execution finished successfully: %s", results)
                result_code["value"] = 0
            except Exception:
                log.exception("Error executing cron reminder run")
            finally:
                finished.set()
                client.disconnect()

        threading.Thread(target=run_once, name="CronReminderRun", daemon=True).start()

    try:
        client.connect()
        finished.wait(timeout=300)
        if not finished.is_set():
            log.error("Cron reminder execution timed out.")
            client.disconnect()
            return 1
        return result_code["value"]
    except Exception:
        log.exception("Could not connect to WhatsApp for cron reminders")
        return 1


if __name__ == "__main__":
    sys.exit(main())
