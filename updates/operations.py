"""Database operations for assignment progress updates."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Assignment, Event

from .models import Update

VALID_ASSIGNMENT_STATUSES = frozenset(
    {"pending", "in_progress", "completed", "cancelled"}
)


def _resolve_assignment(session: Session, id_or_name: str) -> Assignment | None:
    """Resolve an assignment ID, or an event name with one assignment."""
    value = id_or_name.strip()
    if value.isdigit():
        return session.get(Assignment, int(value))

    matches = session.scalars(
        select(Assignment)
        .join(Event, Assignment.event_id == Event.id)
        .where(Event.name == value)
    ).all()
    if len(matches) > 1:
        raise ValueError(f"Event '{value}' has multiple assignments; use the assignment ID.")
    return matches[0] if matches else None


def _require_assignment(session: Session, id_or_name: str) -> Assignment:
    assignment = _resolve_assignment(session, id_or_name)
    if assignment is None:
        raise ValueError(f"Assignment '{id_or_name}' not found.")
    return assignment


def submit_update(session: Session, id_or_name: str, field: str, value: str) -> Update:
    """Create or replace a named field update for an assignment."""
    field = field.strip()
    value = value.strip()
    if not field or not value:
        raise ValueError("Both update field and value are required.")

    assignment = _require_assignment(session, id_or_name)
    update = session.scalar(
        select(Update).where(
            Update.assignment_id == assignment.id,
            Update.field == field,
        )
    )
    now = datetime.now(timezone.utc)
    if update is None:
        update = Update(
            assignment_id=assignment.id,
            field=field,
            value=value,
            timestamp=now,
        )
        session.add(update)
    else:
        update.value = value
        update.timestamp = now

    assignment.last_update_at = now
    session.commit()
    session.refresh(update)
    return update


def get_update_history(session: Session, id_or_name: str) -> list[Update]:
    """Return field updates ordered from oldest to newest."""
    assignment = _require_assignment(session, id_or_name)
    return list(
        session.scalars(
            select(Update)
            .where(Update.assignment_id == assignment.id)
            .order_by(Update.timestamp.asc(), Update.id.asc())
        ).all()
    )


def get_assignment_status(session: Session, id_or_name: str) -> Assignment:
    """Return an assignment or raise the standard not-found error."""
    return _require_assignment(session, id_or_name)


def set_assignment_status(
    session: Session, id_or_name: str, new_status: str
) -> Assignment:
    """Set a validated status on an assignment."""
    new_status = new_status.strip().lower()
    if new_status not in VALID_ASSIGNMENT_STATUSES:
        allowed = ", ".join(sorted(VALID_ASSIGNMENT_STATUSES))
        raise ValueError(f"Invalid status '{new_status}'. Allowed values: {allowed}.")

    assignment = _require_assignment(session, id_or_name)
    assignment.status = new_status
    assignment.last_update_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(assignment)
    return assignment
