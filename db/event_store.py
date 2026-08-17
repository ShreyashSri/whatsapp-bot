"""Persistence layer for events, event labels, and assignments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Assignment, Event, EventLabel, Task, User
from .auth import current_user, jid_user, normalize_jid
from .work_store import WorkStore


class EventTypeLockedError(ValueError):
    """Raised when changing an event's type after dependent data exists."""


EVENT_TYPES = frozenset({"participation", "organization"})
EVENT_CATEGORIES = {
    "participation": frozenset({"gsoc", "lfx", "hacktoberfest", "research", "other"}),
    "organization": frozenset({"recruitment", "hackathon", "workshop", "bootcamp", "other"}),
}


def validate_event_type_category(event_type: str, category: str | None = None) -> tuple[str, str]:
    event_type = event_type.strip().lower()
    category = (category or "other").strip().lower()
    if event_type not in EVENT_TYPES:
        raise ValueError("event type must be `participation` or `organization`")
    if category not in EVENT_CATEGORIES[event_type]:
        raise ValueError(f"{event_type} event category must be one of: {', '.join(sorted(EVENT_CATEGORIES[event_type]))}")
    return event_type, category

def _ensure_user(session: Session, whatsapp_id: str) -> str:
    """Create a bare `users` row if one doesn't exist yet (FK prerequisite for assign)."""
    jid = normalize_jid(whatsapp_id)
    if session.get(User, jid) is None and not any(
        jid_user(candidate.jid) == jid_user(jid) for candidate in session.scalars(select(User)).all()
    ):
        session.add(User(
            jid=jid,
            display_name=jid.split("@")[0],
            role="member",
            active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
        session.flush()
    return jid

def _event_to_dict(session: Session, event: Event) -> dict:
    labels = session.scalars(select(EventLabel.label).where(EventLabel.event_id == event.id)).all()
    direct_assignment_count = session.scalar(
        select(func.count(Assignment.id)).where(Assignment.event_id == event.id)
    ) or 0
    task_assignment_count = session.scalar(
        select(func.count(Assignment.id))
        .join(Task, Assignment.task_id == Task.id)
        .where(Task.event_id == event.id, Task.deleted_at.is_(None))
    ) or 0
    return {
        "id": event.id,
        "name": event.name,
        "type": event.type,
        "category": event.category,
        "description": event.description,
        "status": event.status,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "created_at": event.created_at,
        "deleted_at": event.deleted_at,
        "labels": list(labels),
        # An event's workload includes assignments on its child tasks. The
        # old direct-event-only count made linked work look unassigned.
        "assignment_count": direct_assignment_count + task_assignment_count,
        "direct_assignment_count": direct_assignment_count,
        "task_assignment_count": task_assignment_count,
    }


def _assignment_to_dict(row: Assignment) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "user_id": row.user_jid,
        "status": row.status,
        "missed_count": row.missed_count,
        "last_update_at": row.last_update_at,
    }


class EventStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def _get_active_event(self, session: Session, event_id: int) -> Event:
        """Fetch a non-deleted event or raise. Shared by every mutating method."""
        event = session.get(Event, event_id)
        if event is None or event.deleted_at is not None:
            raise ValueError(f"No active event with id {event_id}")
        return event

    def _set_labels(self, session: Session, event_id: int, labels: list[str]) -> None:
        session.query(EventLabel).filter(EventLabel.event_id == event_id).delete()
        for label in dict.fromkeys(labels):
            session.add(EventLabel(event_id=event_id, label=label))

    def _get_assignment(self, session: Session, event_id: int, user_id: str) -> Assignment | None:
        user_id = normalize_jid(user_id)
        exact = session.scalar(
            select(Assignment).where(Assignment.event_id == event_id, Assignment.user_jid == user_id)
        )
        if exact is not None:
            return exact
        return next((row for row in session.scalars(
            select(Assignment).where(Assignment.event_id == event_id)
        ).all() if jid_user(row.user_jid) == jid_user(user_id)), None)

    def create_event(
        self, *, name: str, type: str, description: str | None = None,
        category: str | None = None,
        labels: list[str] | None = None, start_date: datetime | None = None,
        end_date: datetime | None = None, status: str = "draft",
    ) -> dict:
        type, category = validate_event_type_category(type, category)
        if not name.strip():
            raise ValueError("event name is required")
        with self.session_factory.begin() as session:
            event = Event(
                name=name, type=type, category=category, description=description, status=status,
                start_date=start_date, end_date=end_date, created_at=datetime.now(timezone.utc),
            )
            session.add(event)
            session.flush()  # assigns event.id before we attach labels
            self._set_labels(session, event.id, labels or [])
            session.flush()
            return _event_to_dict(session, event)

    def get_event(self, event_id: int, *, include_deleted: bool = False) -> dict | None:
        with self.session_factory() as session:
            event = session.get(Event, event_id)
            if event is None or (event.deleted_at is not None and not include_deleted):
                return None
            return _event_to_dict(session, event)

    def list_events(
        self, *, status: str | None = None, label: str | None = None, include_deleted: bool = False,
    ) -> list[dict]:
        with self.session_factory() as session:
            query = select(Event)
            if not include_deleted:
                query = query.where(Event.deleted_at.is_(None))
            if status is not None:
                query = query.where(Event.status == status)
            if label is not None:
                query = query.join(EventLabel, EventLabel.event_id == Event.id).where(EventLabel.label == label)
            events = session.scalars(query.order_by(Event.created_at)).all()
            return [_event_to_dict(session, event) for event in events]

    def update_event(
        self, event_id: int, *, name: str | None = None, type: str | None = None,
        description: str | None = None, category: str | None = None, labels: list[str] | None = None,
        start_date: datetime | None = None, end_date: datetime | None = None,
    ) -> dict:
        with self.session_factory.begin() as session:
            event = self._get_active_event(session, event_id)

            if type is not None or category is not None:
                type, category = validate_event_type_category(type or event.type, category or event.category)

            if type is not None and type != event.type:
                has_dependent_data = session.scalar(
                    select(Assignment.id).where(Assignment.event_id == event_id).limit(1)
                )
                if has_dependent_data is not None:
                    raise EventTypeLockedError("Cannot change event type once assignments exist")
                event.type = type
            if category is not None:
                event.category = category

            for field, value in (("name", name), ("description", description),
                                  ("start_date", start_date), ("end_date", end_date)):
                if value is not None:
                    setattr(event, field, value)
            if labels is not None:
                self._set_labels(session, event_id, labels)

            session.flush()
            return _event_to_dict(session, event)

    def set_status(self, event_id: int, status: str) -> dict:
        with self.session_factory.begin() as session:
            event = self._get_active_event(session, event_id)
            event.status = status
            session.flush()
            return _event_to_dict(session, event)

    def delete_event(self, event_id: int) -> bool:
        """Soft-delete an event and all of its active child tasks.

        Work items use soft deletion so assignments, progress revisions, and
        reminder history remain auditable.  A task linked to a deleted event
        must not remain visible as standalone work, though, so both sides of
        the parent/child relationship are retired in the same transaction.
        """
        with self.session_factory.begin() as session:
            event = session.get(Event, event_id)
            if event is None or event.deleted_at is not None:
                return False
            deleted_at = datetime.now(timezone.utc)
            event.deleted_at = deleted_at
            for task in session.scalars(
                select(Task).where(
                    Task.event_id == event_id,
                    Task.deleted_at.is_(None),
                )
            ).all():
                task.deleted_at = deleted_at
                task.updated_at = deleted_at
            return True

    def assign(self, event_id: int, user_id: str) -> dict:
        return WorkStore(self.session_factory).assign("event", event_id, user_id)
    
    def unassign(self, event_id: int, user_id: str) -> bool:
        return WorkStore(self.session_factory).unassign("event", event_id, user_id)

    def list_assignments(self, event_id: int) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(select(Assignment).where(Assignment.event_id == event_id)).all()
            return [_assignment_to_dict(row) for row in rows]

    def get_user_assignments(self, user_id: str) -> list[dict]:
        """Fetch a user's direct events and tasks through the canonical relation."""
        rows = WorkStore(self.session_factory).overview(user_jid=user_id)
        result = []
        for row in rows:
            if row.get("target_type") == "event":
                # Preserve the historical event-assignment response shape.
                result.append({
                    "event_id": row.get("event_id"),
                    "event_name": row.get("name"),
                    "event_type": row.get("event_type"),
                    "status": row.get("status"),
                })
                continue
            result.append({
                "event_id": row.get("parent_event_id"),
                "event_name": row.get("parent_event_name") or row.get("title"),
                "event_type": row.get("event_type") or row.get("target_type"),
                "status": row.get("status"),
                "target_type": "task",
                "task_id": row.get("task_id"),
                "task_name": row.get("title"),
            })
        return result

    def update_user_assignment_status(self, user_id: str, event_id: int, status: str) -> bool:
        """Update the status of a user's own assignment for an event."""
        with self.session_factory.begin() as session:
            assignment = self._get_assignment(session, event_id, user_id)
            if assignment is None:
                return False
            assignment.status = status
            session.flush()
            return True

    def is_admin(self, user_id: str) -> bool:
        """Check if a specific WhatsApp ID belongs to an admin."""
        user = current_user(self.session_factory, user_id)
        return user is not None and user.active and user.role == "admin"
