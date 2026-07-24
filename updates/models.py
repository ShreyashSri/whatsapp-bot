"""Models for the independent Updates module.
Contains stubs for User and Event/Task to allow independent development.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.models import Base


# --- Stubs for Core Entities ---

class UserStub(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    jid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=True)


class EventStub(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)


class TaskStub(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


# --- Updates Module Models ---

class Assignment(Base):
    """Connects a User to an Event or Task."""
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    
    # We allow assignment to either an event or a task. One should be non-null.
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reminder_state: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    missed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    updates: Mapped[list["Update"]] = relationship(
        "Update", back_populates="assignment", cascade="all, delete-orphan"
    )


class Update(Base):
    """Tracks progress against a specific Assignment."""
    __tablename__ = "updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    assignment: Mapped["Assignment"] = relationship("Assignment", back_populates="updates")
