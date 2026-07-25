"""Database models for assignment progress updates.

The core ``Assignment`` model lives in ``db.models``.  Keeping only the
update-specific table here avoids redefining the core users, events, and
assignments tables when this feature is enabled.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models import Assignment, Base


class Update(Base):
    """The latest value submitted for one field of an assignment."""

    __tablename__ = "updates"
    __table_args__ = (
        UniqueConstraint("assignment_id", "field", name="uq_update_assignment_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    assignment: Mapped[Assignment] = relationship()
