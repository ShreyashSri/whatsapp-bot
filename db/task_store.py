"""Task store — CRUD, assignment, and status transitions for Task records (PRD FR-5)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import jid_user, normalize_jid
from .models import Event, Task, User
from .work_store import TASK_LIFECYCLE_TO_PROGRESS, WorkStore

VALID_STATUSES = ("todo", "in_progress", "done", "cancelled")
VALID_PRIORITIES = ("low", "medium", "high")
TASK_STATUS_ALIASES = {
    "pending": "todo",
    "todo": "todo",
    "to do": "todo",
    "to-do": "todo",
    "in_progress": "in_progress",
    "in progress": "in_progress",
    "in-progress": "in_progress",
    "completed": "done",
    "complete": "done",
    "done": "done",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

TASK_TO_PROGRESS_STATUS = TASK_LIFECYCLE_TO_PROGRESS


def normalize_task_status(value: str) -> str:
    """Normalize public task wording to the task table vocabulary."""
    normalized = str(value or "").strip().casefold()
    return TASK_STATUS_ALIASES.get(normalized, normalized)

_TRANSITIONS: dict[str, set[str]] = {
    "todo":        {"in_progress", "done", "cancelled"},
    "in_progress": {"done", "todo", "cancelled"},
    "done":        set(),
    "cancelled":   set(),
}
_UNSET = object()


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
        assignee_jid: str | None = None, assignee_jids: list[str] | None = None,
        due_date: datetime | None = None, priority: str = "medium",
    ) -> Task:
        if not title.strip():
            raise ValueError("title cannot be empty")
        if priority not in VALID_PRIORITIES:
            raise ValueError(f"priority must be one of {VALID_PRIORITIES}")
        now = self._now()
        created_by_jid = normalize_jid(created_by_jid)
        # assignee_jid is the legacy single-person shape; assignee_jids
        # supports creating a task already assigned to several people in one
        # atomic step (e.g. "create task X, assign it to @Alice and @Bob").
        assignees = list(dict.fromkeys(
            normalize_jid(jid)
            for jid in ([assignee_jid] if assignee_jid else []) + list(assignee_jids or [])
            if jid
        ))
        task = Task(
            title=title.strip(), description=description, event_id=event_id,
            assignee_jid=None, status="todo", priority=priority,
            due_date=due_date, created_by_jid=created_by_jid,
            created_at=now, updated_at=now,
        )
        # Create the task and its initial assignment(s) in one transaction so
        # a failure partway through can never leave an orphaned, unassigned
        # task or a task assigned to only some of the requested people.
        with self._sf.begin() as session:
            if event_id is not None:
                event = session.get(Event, event_id)
                if event is None or event.deleted_at is not None:
                    raise ValueError(f"event #{event_id} not found")
            session.add(task)
            session.flush()
            work_store = WorkStore(self._sf)
            for jid in assignees:
                work_store._assign_in(session, "task", task.id, jid)
        return task

    # --- Read ---

    def get(self, task_id: int) -> Task | None:
        with self._sf() as session:
            return self._get(session, task_id)

    def list_all(self, *, status: str | None = None) -> list[Task]:
        status = normalize_task_status(status) if status else None
        if status and status not in VALID_STATUSES:
            raise ValueError("status must be todo, in_progress, done, or cancelled")
        with self._sf() as session:
            q = session.query(Task).filter(Task.deleted_at.is_(None))
            if status:
                q = q.filter(Task.status == status)
            return q.order_by(Task.id).all()

    def list_for_event(self, event_id: int, *, status: str | None = None) -> list[Task]:
        """List active tasks linked to one active event."""
        status = normalize_task_status(status) if status else None
        if status and status not in VALID_STATUSES:
            raise ValueError("status must be todo, in_progress, done, or cancelled")
        with self._sf() as session:
            event = session.get(Event, event_id)
            if event is None or event.deleted_at is not None:
                raise ValueError(f"event #{event_id} not found")
            query = session.query(Task).filter(
                Task.event_id == event_id, Task.deleted_at.is_(None)
            )
            if status:
                query = query.filter(Task.status == status)
            return query.order_by(Task.id).all()

    def list_for_user(self, assignee_jid: str, *, status: str | None = None) -> list[Task]:
        status = normalize_task_status(status) if status else None
        if status and status not in VALID_STATUSES:
            raise ValueError("status must be todo, in_progress, done, or cancelled")
        ids = [
            row["task_id"]
            for row in WorkStore(self._sf).overview(
                user_jid=assignee_jid,
                # Assignment progress and task lifecycle are separate state
                # machines. Resolve the assignment scope first, then filter
                # the returned Task rows by Task.status below.
                status=None,
                target_type="task",
            )
        ]
        if not ids:
            return []
        with self._sf() as session:
            query = session.query(Task).filter(
                Task.id.in_(ids), Task.deleted_at.is_(None)
            )
            if status:
                query = query.filter(Task.status == status)
            return query.order_by(Task.id).all()

    # --- Update ---

    def _sync_assignment_statuses(self, task_id: int, status: str) -> None:
        """Keep every assignment's progress aligned with task lifecycle state.

        Task lifecycle is the durable work-item state while assignment status
        is the per-person view of progress.  They are separate columns, but a
        lifecycle change must not leave the linked assignments reporting an
        older state.  Route the propagation through WorkStore so each change
        still gets its normal append-only status revision.
        """
        progress_status = TASK_TO_PROGRESS_STATUS[status]
        store = WorkStore(self._sf)
        for row in store.overview(target_type="task", target_id=task_id, admin=True):
            if row.get("status") != progress_status:
                store.set_status(
                    str(row["id"]),
                    progress_status,
                    "system@s.whatsapp.net",
                )

    def update(
        self, task_id: int, *, title: str | None = None, description: str | None = None,
        assignee_jid: str | None = None, due_date: datetime | None = None,
        priority: str | None = None, status: str | None = None, force_status: bool = False,
        event_id: int | None | object = _UNSET,
    ) -> Task:
        with self._sf() as session:
            task = self._get(session, task_id)
            if task is None:
                raise ValueError(f"task #{task_id} not found")
            if priority is not None:
                if priority not in VALID_PRIORITIES:
                    raise ValueError(f"priority must be one of {VALID_PRIORITIES}")
                task.priority = priority
            if event_id is not _UNSET:
                if event_id is not None:
                    event = session.get(Event, event_id)
                    if event is None or event.deleted_at is not None:
                        raise ValueError(f"event #{event_id} not found")
                task.event_id = event_id
            if status is not None:
                status = normalize_task_status(status)
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
                # ``TaskStore.update(assignee_jid=...)`` is the legacy
                # single-owner setter. Replace its previous owner; callers
                # that need multiple people use WorkStore.assign_many.
                wanted_user = jid_user(assignee_jid)
                current_rows = WorkStore(self._sf).overview(
                    target_type="task", target_id=task_id, admin=True
                )
                for row in current_rows:
                    if jid_user(row["user_jid"]) != wanted_user:
                        WorkStore(self._sf).unassign("task", task_id, row["user_jid"])
                WorkStore(self._sf).assign("task", task_id, assignee_jid)
            else:
                with self._sf() as session:
                    rows = WorkStore(self._sf).overview(target_type="task", target_id=task_id, admin=True)
                for row in rows:
                    WorkStore(self._sf).unassign("task", task_id, row["user_jid"])
        if status is not None:
            self._sync_assignment_statuses(task_id, status)
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

    def complete(self, task_id: int, actor_jid: str, *, admin: bool = False) -> Task:
        with self._sf() as session:
            task = self._get(session, task_id)
            if task is None:
                raise ValueError(f"task #{task_id} not found")
            if not admin:
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
        self._sync_assignment_statuses(task_id, "done")
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
