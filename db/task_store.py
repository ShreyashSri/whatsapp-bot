"""Task store — CRUD, assignment, and status transitions for Task records (PRD FR-5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from .models import Task
from .auth import jid_user, normalize_jid

VALID_STATUSES = ("todo", "in_progress", "done", "cancelled")
VALID_PRIORITIES = ("low", "medium", "high")

# Allowed status transitions (None = admin-only override)
_TRANSITIONS: dict[str, set[str]] = {
    "todo":        {"in_progress", "cancelled"},
    "in_progress": {"done", "todo", "cancelled"},
    "done":        set(),         # terminal; admin may force via update
    "cancelled":   set(),         # terminal; admin may force via update
}


class TaskStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._sf = session_factory

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _get(self, session: Session, task_id: int) -> Task | None:
        task = session.get(Task, task_id)
        if task is None or task.deleted_at is not None:
            return None
        return task

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        created_by_jid: str,
        *,
        description: str | None = None,
        event_id: int | None = None,
        assignee_jid: str | None = None,
        due_date: datetime | None = None,
        priority: str = "medium",
    ) -> Task:
        if not title.strip():
            raise ValueError("title cannot be empty")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {VALID_PRIORITIES}")
        now = self._now()
        task = Task(
            title=title.strip(),
            description=description,
            event_id=event_id,
            assignee_jid=normalize_jid(assignee_jid) if assignee_jid else None,
            status="todo",
            priority=priority,
            due_date=due_date,
            created_by_jid=normalize_jid(created_by_jid),
            created_at=now,
            updated_at=now,
        )
        with self._sf() as session:
            session.add(task)
            session.commit()
            session.refresh(task)
        return task

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

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
        with self._sf() as session:
            q = session.query(Task).filter(Task.deleted_at.is_(None))
            if status:
                q = q.filter(Task.status == status)
            wanted = jid_user(assignee_jid)
            return [task for task in q.order_by(Task.id).all()
                    if task.assignee_jid and jid_user(task.assignee_jid) == wanted]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(
        self,
        task_id: int,
        *,
        title: str | None = None,
        description: str | None = None,
        assignee_jid: str | None = None,
        due_date: datetime | None = None,
        priority: str | None = None,
        status: str | None = None,
        force_status: bool = False,
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
                    raise ValueError(
                        f"cannot transition task from '{task.status}' to '{status}'"
                    )
                task.status = status
            if title is not None:
                task.title = title.strip() or task.title
            if description is not None:
                task.description = description
            if assignee_jid is not None:
                task.assignee_jid = normalize_jid(assignee_jid) if assignee_jid else None
            if due_date is not None:
                task.due_date = due_date
            task.updated_at = self._now()
            session.commit()
            session.refresh(task)
        return task

    # ------------------------------------------------------------------
    # Assign
    # ------------------------------------------------------------------

    def assign(self, task_id: int, assignee_jid: str) -> Task:
        return self.update(task_id, assignee_jid=assignee_jid)

    def unassign(self, task_id: int) -> Task:
        return self.update(task_id, assignee_jid="")   # empty → None

    # ------------------------------------------------------------------
    # Complete / Transition
    # ------------------------------------------------------------------

    def complete(self, task_id: int, actor_jid: str) -> Task:
        with self._sf() as session:
            task = self._get(session, task_id)
            if task is None:
                raise ValueError(f"task #{task_id} not found")
            if task.assignee_jid and jid_user(task.assignee_jid) != jid_user(actor_jid):
                raise ValueError("only the assignee or an admin can complete this task")
            if task.status == "done":
                raise ValueError("task is already completed")
            if task.status not in _TRANSITIONS or "done" not in _TRANSITIONS[task.status]:
                raise ValueError(f"cannot complete task with status '{task.status}'")
            task.status = "done"
            task.updated_at = self._now()
            session.commit()
            session.refresh(task)
        return task

    # ------------------------------------------------------------------
    # Delete (soft)
    # ------------------------------------------------------------------

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
