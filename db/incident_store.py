"""Persistence compatibility layer for incident deduplication state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import IncidentState


class IncidentStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def read(self) -> dict[str, int]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(IncidentState).order_by(IncidentState.position, IncidentState.incident_url)
            ).all()
            return {row.incident_url: row.status_code for row in rows}

    def write(self, state: dict[str, int]) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory.begin() as session:
            session.execute(delete(IncidentState))
            for position, (url, code) in enumerate(state.items()):
                session.add(
                    IncidentState(
                        incident_url=url,
                        status_code=int(code),
                        last_updated_at=now,
                        position=position,
                    )
                )
