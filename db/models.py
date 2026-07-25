"""Minimal tables used by the bot's persisted features."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# JSONB in PostgreSQL, portable JSON when the stores are exercised with SQLite.
JsonDocument = JSON().with_variant(JSONB, "postgresql")


class User(Base):
    __tablename__ = "users"
    jid: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (CheckConstraint("role IN ('admin', 'member')", name="ck_users_role"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_jid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonDocument, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MediaPost(Base):
    __tablename__ = "media_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_status: Mapped[dict] = mapped_column(JsonDocument, nullable=False)
    # The field is present in the existing JSON entries and is retained for
    # compatibility, although command output does not currently display it.
    created_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON arrays have ordering semantics in the compatibility shape.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MediaPostCounter(Base):
    """High-water mark for the JSON ``nextId`` value."""

    __tablename__ = "media_post_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_id: Mapped[int] = mapped_column(Integer, nullable=False)


class Subgroup(Base):
    __tablename__ = "subgroups"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    member_jids: Mapped[list] = mapped_column(JsonDocument, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IncidentState(Base):
    __tablename__ = "incident_states"

    incident_url: Mapped[str] = mapped_column(Text, primary_key=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
