"""Small persisted state helpers used by the natural-language boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import normalize_jid
from .models import ProcessedMessage, UndoAction


def claim_message(session_factory: Callable[[], Session], message_id: str, actor_jid: str, chat_jid: str) -> bool:
    """Claim one inbound message ID; return False when it was already handled."""
    message_id = str(message_id or "").strip()
    chat_jid = normalize_jid(chat_jid)
    if not message_id or not chat_jid:
        return True
    try:
        with session_factory.begin() as session:
            session.add(ProcessedMessage(
                message_id=message_id,
                chat_jid=chat_jid,
                actor_jid=normalize_jid(actor_jid),
                processed_at=datetime.now(timezone.utc),
            ))
        return True
    except IntegrityError:
        return False


def release_message(session_factory: Callable[[], Session], message_id: str, chat_jid: str) -> None:
    """Release a claimed message ID so it can be retried.

    ``claim_message`` marks a message processed before dispatch runs, so an
    unhandled exception during dispatch (a transient DB/API failure, not a
    user-facing error the handler already reported) would otherwise leave the
    message permanently marked done and it would never be retried even if
    WhatsApp redelivers the same message ID.
    """
    message_id = str(message_id or "").strip()
    chat_jid = normalize_jid(chat_jid)
    if not message_id or not chat_jid:
        return
    with session_factory.begin() as session:
        session.query(ProcessedMessage).filter(
            ProcessedMessage.message_id == message_id,
            ProcessedMessage.chat_jid == chat_jid,
        ).delete()


def record_undo(session_factory: Callable[[], Session], actor_jid: str, operation: str, payload: dict) -> None:
    """Append one reversible action; undo always selects the latest one."""
    with session_factory.begin() as session:
        session.add(UndoAction(
            actor_jid=normalize_jid(actor_jid),
            operation=operation,
            payload=payload,
            created_at=datetime.now(timezone.utc),
        ))


def undo_last(session_factory: Callable[[], Session], actor_jid: str) -> str | None:
    """Undo the latest still-available persisted action for one actor."""
    actor_jid = normalize_jid(actor_jid)
    with session_factory() as session:
        action = session.scalar(
            select(UndoAction)
            .where(UndoAction.actor_jid == actor_jid, UndoAction.consumed_at.is_(None))
            .order_by(UndoAction.id.desc())
        )
        if action is None:
            return None
        operation, payload, action_id = action.operation, dict(action.payload or {}), action.id

    if operation == "event.create":
        from .event_store import EventStore
        if not EventStore(session_factory).delete_event(int(payload["event_id"])):
            raise ValueError("the event no longer exists")
        message = f"↩️ Undid event creation (event {payload['event_id']})."
    elif operation == "task.create":
        from .task_store import TaskStore
        TaskStore(session_factory).delete(int(payload["task_id"]))
        message = f"↩️ Undid task creation (task {payload['task_id']})."
    elif operation == "subgroups.snapshot":
        from .subgroup_store import SubgroupStore
        SubgroupStore(session_factory).write(payload.get("before", {}))
        message = "↩️ Undid the last subgroup/label change."
    elif operation == "assignments.change":
        from .work_store import WorkStore
        store = WorkStore(session_factory)
        target_type, target_id = payload["target_type"], int(payload["target_id"])
        if payload.get("action") == "assign":
            store.unassign_many(target_type, target_id, payload.get("changed", []))
        else:
            store.assign_many(target_type, target_id, payload.get("before", []))
        message = f"↩️ Undid the last assignment change on {target_type} {target_id}."
    elif operation == "assignments.bulk_unassign":
        from .work_store import WorkStore
        store = WorkStore(session_factory)
        items = payload.get("items", [])
        for item in items:
            store.assign_many(
                str(item["target_type"]),
                int(item["target_id"]),
                list(item.get("before", [])),
            )
        message = f"↩️ Undid the last bulk assignment change ({len(items)} work item(s))."
    elif operation == "barrier":
        raise ValueError("the latest action cannot be undone")
    else:
        raise ValueError("the last action cannot be undone")

    with session_factory.begin() as session:
        action = session.get(UndoAction, action_id)
        if action is not None and action.consumed_at is None:
            action.consumed_at = datetime.now(timezone.utc)
    return message
