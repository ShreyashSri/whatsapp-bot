"""Event field schemas — dynamic form definitions and value validation (PRS 7.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import EventFieldSchema

FIELD_TYPES = ("text", "number", "boolean", "date", "url", "single_select", "multi_select", "list")
SELECT_TYPES = ("single_select", "multi_select")
_TRUE = {"true", "yes", "y", "1", "done", "accepted"}
_FALSE = {"false", "no", "n", "0", "pending", "rejected"}


def parse_field_spec(raw: str) -> tuple[str, str, list[str] | None]:
    """Parse `name type` or `name type(a,b,c)` into (name, type, options)."""
    text = raw.strip()
    if not text:
        raise ValueError("field spec cannot be empty")
    options = None
    if text.endswith(")") and "(" in text:
        text, _, option_text = text[:-1].partition("(")
        options = [item.strip() for item in option_text.split(",") if item.strip()]
        if not options:
            raise ValueError("select fields need at least one option")
    name, _, field_type = text.strip().partition(" ")
    name, field_type = name.strip().lower(), field_type.strip().lower() or "text"
    if not name:
        raise ValueError("field name is required")
    if field_type not in FIELD_TYPES:
        raise ValueError(f"field type must be one of: {', '.join(FIELD_TYPES)}")
    if field_type in SELECT_TYPES and not options:
        raise ValueError(f"{field_type} field `{name}` needs options, e.g. `{name} {field_type}(a,b)`")
    if field_type not in SELECT_TYPES:
        options = None
    return name, field_type, options


def coerce_value(field_type: str, value: str, options: list[str] | None) -> str:
    """Validate a submitted value against a field type, returning canonical text."""
    value = value.strip()
    if field_type == "number":
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"expected a number, got `{value}`")
        return str(int(number)) if number.is_integer() else str(number)
    if field_type == "boolean":
        low = value.lower()
        if low in _TRUE:
            return "true"
        if low in _FALSE:
            return "false"
        raise ValueError(f"expected a yes/no value, got `{value}`")
    if field_type == "date":
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"expected a date as YYYY-MM-DD, got `{value}`")
        return value
    if field_type == "url":
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"expected a URL starting with http:// or https://, got `{value}`")
        return value
    if field_type == "single_select":
        match = next((option for option in options or [] if option.lower() == value.lower()), None)
        if match is None:
            raise ValueError(f"expected one of: {', '.join(options or [])}")
        return match
    if field_type == "multi_select":
        chosen = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            match = next((option for option in options or [] if option.lower() == item.lower()), None)
            if match is None:
                raise ValueError(f"`{item}` is not valid; expected values from: {', '.join(options or [])}")
            chosen.append(match)
        if not chosen:
            raise ValueError(f"expected one or more of: {', '.join(options or [])}")
        return ", ".join(chosen)
    if field_type == "list":
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("expected a comma-separated list")
        return ", ".join(items)
    return value


def validate_submission(session: Session, event_id: int, field: str, value: str) -> tuple[str, str]:
    """Apply an event's schema to one submitted field.

    Events without a schema stay free-form so existing usage keeps working.
    """
    rows = session.scalars(
        select(EventFieldSchema).where(EventFieldSchema.event_id == event_id)
    ).all()
    if not rows:
        return field, value
    spec = next((row for row in rows if row.name == field.lower()), None)
    if spec is None:
        valid = ", ".join(sorted(row.name for row in rows))
        raise ValueError(f"`{field}` is not a field on this event. Valid fields: {valid}")
    return spec.name, coerce_value(spec.field_type, value, spec.options)


class SchemaStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def list_fields(self, event_id: int) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(EventFieldSchema)
                .where(EventFieldSchema.event_id == event_id)
                .order_by(EventFieldSchema.position, EventFieldSchema.id)
            ).all()
            return [{"name": row.name, "field_type": row.field_type, "options": row.options}
                    for row in rows]

    def set_fields(self, event_id: int, specs: list[str]) -> list[dict]:
        """Replace an event's whole schema definition."""
        parsed = [parse_field_spec(spec) for spec in specs if spec.strip()]
        if not parsed:
            raise ValueError("define at least one field")
        names = [name for name, _, _ in parsed]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
        with self.session_factory.begin() as session:
            session.execute(delete(EventFieldSchema).where(EventFieldSchema.event_id == event_id))
            for position, (name, field_type, options) in enumerate(parsed):
                session.add(EventFieldSchema(event_id=event_id, name=name, field_type=field_type,
                                             options=options, position=position))
        return self.list_fields(event_id)

    def add_field(self, event_id: int, spec: str) -> list[dict]:
        name, field_type, options = parse_field_spec(spec)
        with self.session_factory.begin() as session:
            existing = session.scalar(select(EventFieldSchema).where(
                EventFieldSchema.event_id == event_id, EventFieldSchema.name == name))
            if existing is not None:
                existing.field_type, existing.options = field_type, options
            else:
                highest = session.scalar(select(EventFieldSchema.position).where(
                    EventFieldSchema.event_id == event_id).order_by(EventFieldSchema.position.desc()))
                session.add(EventFieldSchema(event_id=event_id, name=name, field_type=field_type,
                                             options=options, position=(highest or 0) + 1))
        return self.list_fields(event_id)

    def remove_field(self, event_id: int, name: str) -> bool:
        with self.session_factory.begin() as session:
            row = session.scalar(select(EventFieldSchema).where(
                EventFieldSchema.event_id == event_id, EventFieldSchema.name == name.strip().lower()))
            if row is None:
                return False
            session.delete(row)
            return True

    def clear(self, event_id: int) -> int:
        with self.session_factory.begin() as session:
            rows = session.scalars(select(EventFieldSchema).where(
                EventFieldSchema.event_id == event_id)).all()
            for row in rows:
                session.delete(row)
            return len(rows)
