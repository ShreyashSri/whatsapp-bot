"""Persistence layer for events, event labels, and assignments."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Assignment, Event, EventLabel, User


class EventTypeLockedError(ValueError):
    """Raised when changing an event's type after dependent data exists."""

def _ensure_user(session: Session, whatsapp_id: str) -> None:
    """Create a bare `users` row if one doesn't exist yet (FK prerequisite for assign)."""
    if session.scalar(select(User).where(User.whatsapp_id == whatsapp_id)) is None:
        session.add(User(
            whatsapp_id=whatsapp_id,
            role="member",
            is_active=True,
            created_at=datetime.now(timezone.utc),
        ))
        session.flush()

def _event_to_dict(session: Session, event: Event) -> dict:
    labels = session.scalars(select(EventLabel.label).where(EventLabel.event_id == event.id)).all()
    assignment_count = session.scalar(
        select(func.count(Assignment.id)).where(Assignment.event_id == event.id)
    ) or 0
    return {
        "id": event.id,
        "name": event.name,
        "type": event.type,
        "description": event.description,
        "status": event.status,
        "start_date": event.start_date,
        "end_date": event.end_date,
        "created_at": event.created_at,
        "deleted_at": event.deleted_at,
        "labels": list(labels),
        "assignment_count": assignment_count,
    }


def _assignment_to_dict(row: Assignment) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "user_id": row.user_id,
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
        return session.scalar(
            select(Assignment).where(Assignment.event_id == event_id, Assignment.user_id == user_id)
        )

    def create_event(
        self, *, name: str, type: str, description: str | None = None,
        labels: list[str] | None = None, start_date: datetime | None = None,
        end_date: datetime | None = None, status: str = "draft",
    ) -> dict:
        with self.session_factory.begin() as session:
            event = Event(
                name=name, type=type, description=description, status=status,
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
        description: str | None = None, labels: list[str] | None = None,
        start_date: datetime | None = None, end_date: datetime | None = None,
    ) -> dict:
        with self.session_factory.begin() as session:
            event = self._get_active_event(session, event_id)

            if type is not None and type != event.type:
                has_dependent_data = session.scalar(
                    select(Assignment.id).where(Assignment.event_id == event_id).limit(1)
                )
                if has_dependent_data is not None:
                    raise EventTypeLockedError("Cannot change event type once assignments exist")
                event.type = type

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
        """Soft-delete if assignments exist; hard-delete otherwise."""
        with self.session_factory.begin() as session:
            event = session.get(Event, event_id)
            if event is None or event.deleted_at is not None:
                return False

            has_dependent_data = session.scalar(
                select(Assignment.id).where(Assignment.event_id == event_id).limit(1)
            )
            if has_dependent_data is not None:
                event.deleted_at = datetime.now(timezone.utc)
            else:
                session.query(EventLabel).filter(EventLabel.event_id == event_id).delete()
                session.delete(event)
            return True

    def assign(self, event_id: int, user_id: str) -> dict:
        with self.session_factory.begin() as session:
            self._get_active_event(session, event_id)
            existing = self._get_assignment(session, event_id, user_id)
            if existing is not None:
                return _assignment_to_dict(existing)

            _ensure_user(session, user_id)

            assignment = Assignment(
                event_id=event_id, user_id=user_id, status="pending",
                created_at=datetime.now(timezone.utc),
            )
            session.add(assignment)
            session.flush()
            return _assignment_to_dict(assignment)
    
    def unassign(self, event_id: int, user_id: str) -> bool:
        with self.session_factory.begin() as session:
            assignment = self._get_assignment(session, event_id, user_id)
            if assignment is None:
                return False
            session.delete(assignment)
            return True

    def list_assignments(self, event_id: int) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(select(Assignment).where(Assignment.event_id == event_id)).all()
            return [_assignment_to_dict(row) for row in rows]

    def get_user_assignments(self, user_id: str) -> list[dict]:
        """Fetch all event assignments for a specific user, joined with event info."""
        with self.session_factory() as session:
            stmt = (
                select(Assignment, Event)
                .join(Event, Assignment.event_id == Event.id)
                .where(Assignment.user_id == user_id)
            )
            return [
                {
                    "event_id": event.id,
                    "event_name": event.name,
                    "event_type": event.type,
                    "status": assignment.status,
                }
                for assignment, event in session.execute(stmt).all()
            ]

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
        with self.session_factory() as session:
            user = session.scalar(
                select(User).where(User.whatsapp_id == user_id)
            )
            return user is not None and user.role == "admin"