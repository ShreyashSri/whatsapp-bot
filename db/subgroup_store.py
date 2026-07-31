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

    def add_members(self, name: str, members: list[str]) -> tuple[list[str], int]:
        """Atomically add members to one named collection.

        The legacy ``read``/``write`` shape remains available for migrations,
        but live mutations must lock and update only the requested row. This
        prevents two simultaneous label/subgroup changes from replacing the
        entire table with stale snapshots.
        """
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(Subgroup).where(Subgroup.name == name).with_for_update()
            )
            if row is None:
                row = Subgroup(name=name, member_jids=[], position=0)
                session.add(row)
                session.flush()

            current = list(row.member_jids or [])
            existing = {jid.casefold() for jid in current}
            added: list[str] = []
            for member in members:
                key = member.casefold()
                if key not in existing:
                    current.append(member)
                    existing.add(key)
                    added.append(member)
            row.member_jids = current
            return added, len(current)

    def remove_members(self, name: str, members: set[str]) -> tuple[int, int, bool]:
        """Atomically remove members, deleting the collection when empty."""
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(Subgroup).where(Subgroup.name == name).with_for_update()
            )
            if row is None:
                raise ValueError("collection not found")
            current = list(row.member_jids or [])
            remaining = [
                member
                for member in current
                if member.casefold() not in members
                and member.split("@", 1)[0].casefold() not in members
            ]
            removed = len(current) - len(remaining)
            if remaining:
                row.member_jids = remaining
            else:
                session.delete(row)
            return removed, len(remaining), not remaining

    def delete(self, name: str) -> bool:
        """Delete one collection without rewriting unrelated rows."""
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(Subgroup).where(Subgroup.name == name).with_for_update()
            )
            if row is None:
                return False
            session.delete(row)
            return True
