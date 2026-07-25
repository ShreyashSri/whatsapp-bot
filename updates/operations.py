"""Database operations for assignment progress updates."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Assignment, Event, ProgressRevision, Task
from db.work_store import normalize_reference

VALID_ASSIGNMENT_STATUSES = frozenset(
    {"pending", "in_progress", "completed", "cancelled"}
)


def _resolve_assignment(session: Session, id_or_name: str) -> Assignment | None:
    """Resolve an assignment ID, or an event name with one assignment."""
    value = normalize_reference(id_or_name)
    if value.isdigit():
        return session.get(Assignment, int(value))

    import re
    typed = re.fullmatch(r"(event|task):(\d+)(?:@(.+))?", value, re.I)
    if typed:
        typ, ident, jid = typed.groups()
        column = Assignment.event_id if typ.lower() == "event" else Assignment.task_id
        rows = session.scalars(select(Assignment).where(column == int(ident))).all()
        if jid:
            from db.auth import jid_user
            return next((row for row in rows if jid_user(row.user_jid) == jid_user(jid)), None)
        return rows[0] if rows else None

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


def submit_update(session: Session, id_or_name: str, field: str, value: str,
                  author_jid: str = "system@s.whatsapp.net") -> ProgressRevision:
    """Append a named field revision; never overwrite prior progress."""
    field = field.strip()
    value = value.strip()
    if not field or not value:
        raise ValueError("Both update field and value are required.")

    assignment = _require_assignment(session, id_or_name)
    now = datetime.now(timezone.utc)
    previous = session.scalar(select(ProgressRevision).where(
        ProgressRevision.assignment_id == assignment.id, ProgressRevision.field == field
    ).order_by(ProgressRevision.id.desc()))
    update = ProgressRevision(assignment_id=assignment.id, field=field, value=value,
                              author_jid=author_jid, timestamp=now,
                              superseded_revision_id=previous.id if previous else None)
    session.add(update)

    assignment.last_update_at = now
    assignment.missed_count = 0
    assignment.reminder_state = None
    session.commit()
    session.refresh(update)
    return update


def edit_update(session: Session, update_id: str, new_value: str,
                author_jid: str = "system@s.whatsapp.net") -> ProgressRevision:
    """Edit by appending a revision that points to the superseded revision."""
    if not update_id.strip().isdigit():
        raise ValueError("Update ID must be numeric.")

    new_value = new_value.strip()
    if not new_value:
        raise ValueError("The new update value is required.")

    old = session.get(ProgressRevision, int(update_id))
    if old is None:
        raise ValueError(f"Update '{update_id}' not found.")
    now = datetime.now(timezone.utc)
    update = ProgressRevision(assignment_id=old.assignment_id, field=old.field, value=new_value,
                              author_jid=author_jid, timestamp=now,
                              superseded_revision_id=old.id)
    session.add(update)
    assignment = session.get(Assignment, old.assignment_id)
    if assignment is not None:
        assignment.last_update_at = now
        assignment.missed_count = 0
        assignment.reminder_state = None
    session.commit()
    session.refresh(update)
    return update


def get_update_history(session: Session, id_or_name: str) -> list[ProgressRevision]:
    """Return progress revisions ordered from oldest to newest."""
    assignment = _require_assignment(session, id_or_name)
    return list(
        session.scalars(
            select(ProgressRevision)
            .where(ProgressRevision.assignment_id == assignment.id)
            .order_by(ProgressRevision.timestamp.asc(), ProgressRevision.id.asc())
        ).all()
    )


def get_assignment_status(session: Session, id_or_name: str) -> Assignment:
    """Return an assignment or raise the standard not-found error."""
    return _require_assignment(session, id_or_name)


def set_assignment_status(
    session: Session, id_or_name: str, new_status: str,
    author_jid: str = "system@s.whatsapp.net"
) -> Assignment:
    """Set a validated status on an assignment."""
    new_status = new_status.strip().lower()
    if new_status not in VALID_ASSIGNMENT_STATUSES:
        allowed = ", ".join(sorted(VALID_ASSIGNMENT_STATUSES))
        raise ValueError(f"Invalid status '{new_status}'. Allowed values: {allowed}.")

    assignment = _require_assignment(session, id_or_name)
    now = datetime.now(timezone.utc)
    previous = session.scalar(select(ProgressRevision).where(ProgressRevision.assignment_id == assignment.id,
                                                              ProgressRevision.field == "status").order_by(ProgressRevision.id.desc()))
    assignment.status = new_status
    assignment.last_update_at = now
    assignment.missed_count = 0
    assignment.reminder_state = None
    session.add(ProgressRevision(assignment_id=assignment.id, field="status", value=new_status,
                                 author_jid=author_jid, timestamp=now,
                                 superseded_revision_id=previous.id if previous else None))
    session.commit()
    session.refresh(assignment)
    return assignment
