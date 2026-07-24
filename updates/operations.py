"""Deterministic business logic operations for the Updates module.
These functions do not interact with the WhatsApp client directly.
They only take a database session and the required inputs.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from updates.models import Assignment, Update


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _resolve_assignment(session: Session, id_or_name: str) -> Optional[Assignment]:
    """Look up an Assignment by numeric ID or human-readable name.
    
    Accepts inputs like:
        "1"             -> looks up by primary key
        "Bibisha_gsoc"  -> looks up by name field
    """
    if id_or_name.isdigit():
        return session.query(Assignment).filter(Assignment.id == int(id_or_name)).first()
    return session.query(Assignment).filter(Assignment.name == id_or_name).first()


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def submit_update(session: Session, id_or_name: str, field: str, value: str) -> Update:
    """Submit a new update for an assignment, overwriting if the field already exists."""
    assignment = _resolve_assignment(session, id_or_name)
    if not assignment:
        raise ValueError(f"Assignment '{id_or_name}' not found.")

    # Check if this field already exists for this assignment
    existing_update = session.query(Update).filter_by(
        assignment_id=assignment.id,
        field=field
    ).first()

    from datetime import datetime, timezone

    if existing_update:
        existing_update.value = value
        existing_update.timestamp = datetime.now(timezone.utc)
        session.commit()
        session.refresh(existing_update)
        return existing_update
    else:
        new_update = Update(
            assignment_id=assignment.id,
            field=field,
            value=value,
        )
        session.add(new_update)
        session.commit()
        session.refresh(new_update)
        return new_update




def get_update_history(session: Session, id_or_name: str) -> List[Update]:
    """Retrieve the update history for an assignment (by ID or name)."""
    assignment = _resolve_assignment(session, id_or_name)
    if not assignment:
        raise ValueError(f"Assignment '{id_or_name}' not found.")
        
    return session.query(Update).filter(
        Update.assignment_id == assignment.id
    ).order_by(Update.timestamp.asc()).all()


def get_assignment_status(session: Session, id_or_name: str) -> Optional[Assignment]:
    """Retrieve an assignment to view its status (by ID or name)."""
    return _resolve_assignment(session, id_or_name)


def set_assignment_status(session: Session, id_or_name: str, new_status: str) -> Assignment:
    """Set the overall status of an assignment."""
    assignment = _resolve_assignment(session, id_or_name)
    if not assignment:
        raise ValueError(f"Assignment '{id_or_name}' not found.")
        
    assignment.status = new_status
    session.commit()
    session.refresh(assignment)
    return assignment
