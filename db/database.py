"""Shared SQLAlchemy engine and session setup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _normalise_url(database_url: str) -> str:
    """Make common PostgreSQL URLs explicit about the psycopg driver."""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://"):]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://"):]
    return database_url


@dataclass(frozen=True)
class Database:
    """The one engine/session factory shared by all feature stores."""

    engine: Engine
    session_factory: Callable[[], Session]

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)


def create_database(database_url: str) -> Database:
    if not database_url:
        raise ValueError("DATABASE_URL is required")

    engine = create_engine(_normalise_url(database_url), pool_pre_ping=True)
    return Database(
        engine=engine,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
    )
