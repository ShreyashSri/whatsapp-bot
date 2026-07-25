"""Task store — CRUD, assignment, and status transitions for Task records (PRD FR-5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import jid_user, normalize_jid
from .models import Task, User
from .work_store import WorkStore

VALID_STATUSES = ("todo", "in_progress", "done", "cancelled")
VALID_PRIORITIES = ("low", "medium", "high")

_TRANSITIONS: dict[str, set[str]] = {
    "todo":        {"in_progress", "done", "cancelled"},
    "in_progress": {"done", "todo", "cancelled"},
    "done":        set(),
    "cancelled":   set(),
}


class TaskStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sf = session_factory

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _get(self, session: Session, task_id: int) -> Task | None:
        task = session.get(Task, task_id)
        return None if task is None or task.deleted_at is not None else task

    def _ensure_user(self, session: Session, jid: str) -> str:
        """Ensure an assignee exists in the shared users table."""
        normalized = normalize_jid(jid)
        if not normalized:
            raise ValueError("assignee must be a valid user")
        existing = session.get(User, normalized)
        if existing is not None:
            return existing.jid
        equivalent = next(
            (candidate for candidate in session.scalars(select(User)).all()
             if jid_user(candidate.jid) == jid_user(normalized)),
            None,
        )
        if equivalent is not None:
            return equivalent.jid
        if existing is None:
            now = self._now()
            session.add(User(
                jid=normalized,
                display_name=normalized.split("@", 1)[0],
                role="member",
                active=True,
                created_at=now,
                updated_at=now,
            ))
            session.flush()
        return normalized

    # --- Create ---

    def create(
        self, title: str, created_by_jid: str, *,
        description: str | None = None, event_id: int | None = None,
        assignee_jid: str | None = None, due_date: datetime | None = None,
        priority: str = "medium",
    ) -> Task:
        if not title.strip():
            raise ValueError("title cannot be empty")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {VALID_PRIORITIES}")
        now = self._now()
        created_by_jid = normalize_jid(created_by_jid)
        assignee_jid = normalize_jid(assignee_jid) if assignee_jid else None
        task = Task(
            title=title.strip(), description=description, event_id=event_id,
            assignee_jid=None, status="todo", priority=priority,
            due_date=due_date, created_by_jid=created_by_jid,
            created_at=now, updated_at=now,
        )
        with self._sf() as session:
            session.add(task)
            session.commit()
            session.refresh(task)
        if assignee_jid:
            WorkStore(self._sf).assign("task", task.id, assignee_jid)
        return task

    # --- Read ---

    def get(self, task_id: int) -> Task | None:
        with self._sf() as session:
            return self._get(session, task_id)

    def list_all(self, *, status: str | None = None) -> list[Task]:
        with self._sf() as session:
            q = session.query(Task).filter(Task.deleted_at.is_(None))
            if status:
                q = q.filter(Task.status == status)
            return q.order_by(Task.id).all()

    def list_for_user(self, assignee_jid: str, *, status: str | None = None) -> list[Task]:
        ids = [row["task_id"] for row in WorkStore(self._sf).overview(user_jid=assignee_jid, status=status, target_type="task")]
        with self._sf() as session:
            return [session.get(Task, task_id) for task_id in ids]

    # --- Update ---

    def update(
        self, task_id: int, *, title: str | None = None, description: str | None = None,
        assignee_jid: str | None = None, due_date: datetime | None = None,
        priority: str | None = None, status: str | None = None, force_status: bool = False,
    ) -> Task:
        with self._sf() as session:
            task = self._get(session, task_id)
            if task is None:
                raise ValueError(f"task #{task_id} not found")
            if priority is not None:
                if priority not in VALID_PRIORITIES:
                    raise ValueError(f"priority must be one of {VALID_PRIORITIES}")
                task.priority = priority
            if status is not None:
                if status not in VALID_STATUSES:
                    raise ValueError(f"status must be one of {VALID_STATUSES}")
                if not force_status and status not in _TRANSITIONS.get(task.status, set()):
                    raise ValueError(f"cannot transition from '{task.status}' to '{status}'")
                task.status = status
            if title is not None:
                task.title = title.strip() or task.title
            if description is not None:
                task.description = description
            if assignee_jid is not None:
                # Assignment is the source of truth; keep the old column only
                # for databases that still have it during migration.
                task.assignee_jid = None
            if due_date is not None:
                task.due_date = due_date
            task.updated_at = self._now()
            session.commit()
            session.refresh(task)
        if assignee_jid is not None:
            if assignee_jid:
                WorkStore(self._sf).assign("task", task_id, assignee_jid)
            else:
                with self._sf() as session:
                    rows = WorkStore(self._sf).overview(target_type="task", target_id=task_id, admin=True)
                for row in rows:
                    WorkStore(self._sf).unassign("task", task_id, row["user_jid"])
        if status == "done":
            for row in WorkStore(self._sf).overview(target_type="task", target_id=task_id, admin=True):
                if row.get("status") != "completed":
                    WorkStore(self._sf).set_status(str(row["id"]), "completed", "system@s.whatsapp.net")
        return task

    # --- Assign / Unassign ---

    def assign(self, task_id: int, assignee_jid: str) -> Task:
        WorkStore(self._sf).assign("task", task_id, assignee_jid)
        return self.get(task_id)

    def unassign(self, task_id: int) -> Task:
        rows = WorkStore(self._sf).overview(target_type="task", target_id=task_id, admin=True)
        for row in rows:
            WorkStore(self._sf).unassign("task", task_id, row["user_jid"])
        return self.get(task_id)

    # --- Complete ---

    def complete(self, task_id: int, actor_jid: str) -> Task:
        with self._sf() as session:
            task = self._get(session, task_id)
            if task is None:
                raise ValueError(f"task #{task_id} not found")
            assignments = WorkStore(self._sf).overview(user_jid=actor_jid, target_type="task", target_id=task_id)
            if not assignments:
                raise ValueError("only the assignee or an admin can complete this task")
            if task.status == "done":
                raise ValueError("task is already completed")
            if "done" not in _TRANSITIONS.get(task.status, set()):
                raise ValueError(f"cannot complete task with status '{task.status}'")
            task.status = "done"
            task.updated_at = self._now()
            session.commit()
            session.refresh(task)
        for row in assignments:
            WorkStore(self._sf).set_status(f"{row['id']}", "completed", actor_jid)
        return task

    # --- Delete (soft) ---

    def delete(self, task_id: int) -> Task:
        with self._sf() as session:
            task = self._get(session, task_id)
            if task is None:
                raise ValueError(f"task #{task_id} not found")
            task.deleted_at = self._now()
            task.updated_at = self._now()
            session.commit()
            session.refresh(task)
        return task
