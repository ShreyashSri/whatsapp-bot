"""Persistence compatibility layer for the media task manager."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import MediaPost, MediaPostCounter


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _timestamp_value(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class MediaStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def read(self) -> dict:
        with self.session_factory() as session:
            rows = session.scalars(
                select(MediaPost).order_by(MediaPost.state, MediaPost.position, MediaPost.id)
            ).all()
            counter = session.get(MediaPostCounter, 1)
            next_id = counter.next_id if counter else (max((row.id for row in rows), default=0) + 1)

            state = {"nextId": next_id, "todo": [], "posted": []}
            for row in rows:
                entry = {
                    "id": row.id,
                    "text": row.text,
                    "createdAt": _timestamp_value(row.created_at),
                    "platforms": dict(row.platform_status or {}),
                }
                if row.created_by is not None:
                    entry["createdBy"] = row.created_by
                if row.posted_at is not None:
                    entry["postedAt"] = _timestamp_value(row.posted_at)
                state[row.state].append(entry)
            return state

    def write(self, state: dict) -> None:
        """Replace the compatibility snapshot in one transaction."""
        with self.session_factory.begin() as session:
            session.execute(delete(MediaPost))
            for state_name in ("todo", "posted"):
                for position, entry in enumerate(state.get(state_name, [])):
                    session.add(
                        MediaPost(
                            id=int(entry["id"]),
                            text=str(entry.get("text", "")),
                            state=state_name,
                            created_at=_parse_timestamp(entry.get("createdAt")),
                            posted_at=(
                                _parse_timestamp(entry["postedAt"])
                                if entry.get("postedAt")
                                else None
                            ),
                            platform_status=dict(entry.get("platforms") or {}),
                            created_by=entry.get("createdBy"),
                            position=position,
                        )
                    )

            counter = session.get(MediaPostCounter, 1)
            next_id = int(state.get("nextId", 1))
            if counter is None:
                session.add(MediaPostCounter(id=1, next_id=next_id))
            else:
                counter.next_id = next_id
