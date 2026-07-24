"""One-time import of the bot's legacy JSON state files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .incident_store import IncidentStore
from .media_store import MediaStore
from .models import IncidentState, MediaPost, Subgroup
from .subgroup_store import SubgroupStore

log = logging.getLogger(__name__)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not import %s: %s", path.name, exc)
        return None


def _is_empty(session_factory: Callable[[], Session], model) -> bool:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(model)) == 0


def migrate_legacy_json(session_factory: Callable[[], Session], data_dir: Path) -> None:
    """Import each JSON file only when its corresponding table is empty."""
    posts_path = data_dir / "posts.json"
    if _is_empty(session_factory, MediaPost):
        data = _load_json(posts_path)
        if isinstance(data, dict):
            MediaStore(session_factory).write({
                "nextId": data.get("nextId", 1),
                "todo": data.get("todo", []) if isinstance(data.get("todo", []), list) else [],
                "posted": data.get("posted", []) if isinstance(data.get("posted", []), list) else [],
            })
            log.info("Imported legacy %s", posts_path.name)

    subgroups_path = data_dir / "subgroups.json"
    if _is_empty(session_factory, Subgroup):
        data = _load_json(subgroups_path)
        if isinstance(data, dict):
            clean = {
                name: members
                for name, members in data.items()
                if isinstance(name, str)
                and isinstance(members, list)
                and all(isinstance(jid, str) for jid in members)
            }
            SubgroupStore(session_factory).write(clean)
            log.info("Imported legacy %s", subgroups_path.name)

    incident_path = data_dir / "incident_state.json"
    if _is_empty(session_factory, IncidentState):
        data = _load_json(incident_path)
        if isinstance(data, dict):
            clean = {}
            for url, code in data.items():
                try:
                    clean[str(url)] = int(code)
                except (TypeError, ValueError):
                    continue
            IncidentStore(session_factory).write(clean)
            log.info("Imported legacy %s", incident_path.name)
