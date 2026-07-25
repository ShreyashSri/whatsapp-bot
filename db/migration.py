"""One-time import of the bot's legacy JSON state files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from .incident_store import IncidentStore
from .media_store import MediaStore
from .models import Assignment, IncidentState, MediaPost, ProgressRevision, Subgroup, Task
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


def migrate_unified_work(session_factory: Callable[[], Session]) -> None:
    """Idempotently move legacy task assignees and mutable updates forward.

    This is deliberately data-only: deployments may run it after ``create_all``
    or from a schema migration tool, and it never deletes historical rows.
    """
    try:
        from updates.models import Update
    except ImportError:
        Update = None
    with session_factory.begin() as session:
        for row in session.scalars(select(Assignment)).all():
            if not row.target_type:
                row.target_type = "event" if row.event_id is not None else "task"
        tasks = session.scalars(select(Task).where(Task.assignee_jid.is_not(None))).all()
        from .work_store import WorkStore
        for task in tasks:
            existing = session.scalar(select(Assignment).where(Assignment.task_id == task.id,
                                                                Assignment.user_jid == task.assignee_jid))
            if existing is None:
                # The user row is expected to exist in normal operation; the
                # service also handles old imports that did not create one.
                jid = WorkStore._ensure_user(session, task.assignee_jid)
                session.add(Assignment(target_type="task", task_id=task.id, user_jid=jid,
                                       status="completed" if task.status == "done" else "pending",
                                       created_at=task.created_at))
            task.assignee_jid = None
        if Update is not None:
            for old in session.scalars(select(Update)).all():
                already = session.get(ProgressRevision, old.id)
                if already is None:
                    session.add(ProgressRevision(id=old.id, assignment_id=old.assignment_id,
                        field=old.field, value=old.value,
                        author_jid="system@s.whatsapp.net", timestamp=old.timestamp))


def upgrade_unified_schema(database) -> None:
    """Add unified-work columns to databases created by the old schema."""
    engine = database.engine
    tables = inspect(engine).get_table_names()
    if "assignments" not in tables:
        database.initialize()
        return
    columns = {column["name"] for column in inspect(engine).get_columns("assignments")}
    statements = []
    if "task_id" not in columns:
        statements.append("ALTER TABLE assignments ADD COLUMN task_id INTEGER REFERENCES tasks(id)")
    if "target_type" not in columns:
        statements.append("ALTER TABLE assignments ADD COLUMN target_type VARCHAR(8) NOT NULL DEFAULT 'event'")
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        if engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE assignments ALTER COLUMN event_id DROP NOT NULL"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_task_user ON assignments(task_id, user_jid) WHERE task_id IS NOT NULL"))
            connection.execute(text("ALTER TABLE assignments DROP CONSTRAINT IF EXISTS ck_assignment_one_target"))
            connection.execute(text("ALTER TABLE assignments ADD CONSTRAINT ck_assignment_one_target CHECK ((event_id IS NOT NULL AND task_id IS NULL) OR (event_id IS NULL AND task_id IS NOT NULL))"))
    event_columns = {column["name"] for column in inspect(engine).get_columns("events")}
    if "category" not in event_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE events ADD COLUMN category VARCHAR(32) NOT NULL DEFAULT 'other'"))
    # Creates progress_revisions and any other newly declared tables without
    # modifying existing tables.
    database.initialize()
