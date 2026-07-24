"""Persistence compatibility layer for global custom subgroups."""

from __future__ import annotations

from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Subgroup


class SubgroupStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def read(self) -> dict[str, list[str]]:
        with self.session_factory() as session:
            rows = session.scalars(select(Subgroup).order_by(Subgroup.position, Subgroup.name)).all()
            return {
                row.name: [jid for jid in (row.member_jids or []) if isinstance(jid, str)]
                for row in rows
            }

    def write(self, data: dict[str, list[str]]) -> None:
        with self.session_factory.begin() as session:
            session.execute(delete(Subgroup))
            for position, (name, members) in enumerate(data.items()):
                session.add(
                    Subgroup(
                        name=name,
                        member_jids=[jid for jid in members if isinstance(jid, str)],
                        position=position,
                    )
                )
