"""Deterministic business logic operations for the Updates module.
These functions do not interact with the WhatsApp client directly.
They only take a database session and the required inputs.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from updates.models import Assignment, Update


def submit_update(session: Session, assignment_id: int, field: str, value: str) -> Update:
    """Submit a new update for an assignment."""
    assignment = session.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise ValueError(f"Assignment {assignment_id} not found.")

    new_update = Update(
        assignment_id=assignment.id,
        field=field,
        value=value,
    )
    session.add(new_update)
    session.commit()
    session.refresh(new_update)
    return new_update


def edit_update(session: Session, update_id: int, new_value: str) -> Update:
    """Edit an existing update."""
    update_record = session.query(Update).filter(Update.id == update_id).first()
    if not update_record:
        raise ValueError(f"Update {update_id} not found.")
        
    update_record.value = new_value
    session.commit()
    session.refresh(update_record)
    return update_record


def get_update_history(session: Session, assignment_id: int) -> List[Update]:
    """Retrieve the update history for an assignment."""
    assignment = session.query(Assignment).filter(Assignment.id == assignment_id).first()
    if not assignment:
        raise ValueError(f"Assignment {assignment_id} not found.")
        
    return session.query(Update).filter(
        Update.assignment_id == assignment_id
    ).order_by(Update.timestamp.asc()).all()

def get_assignment_status(session: Session, assignment_id: int) -> Optional[Assignment]:
    """Retrieve an assignment to view its status."""
    return session.query(Assignment).filter(Assignment.id == assignment_id).first()
