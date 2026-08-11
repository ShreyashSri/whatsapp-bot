"""Reminder store — configuration, scheduled execution, idempotency, and attempt logs (PRD FR-7)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import audit, normalize_jid
from .models import Assignment, Event, ReminderConfig, ReminderLog, Task, User

log = logging.getLogger(__name__)
# Reminders keep going until the work is closed or deleted.
CLOSED_EVENT_STATUSES = frozenset({"completed", "cancelled"})
CLOSED_TASK_STATUSES = frozenset({"done", "cancelled"})


def _as_utc(value: datetime | None) -> datetime | None:
    """Backends without timezone support return naive datetimes; treat them as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _make_neonize_jid(jid_str: str) -> Any:
    norm = normalize_jid(jid_str)
    if "@" in norm:
        u, s = norm.split("@", 1)
    else:
        u, s = norm, "s.whatsapp.net"
    try:
        from neonize.utils import build_jid
        return build_jid(u, s)
    except Exception:
        return norm


class ReminderStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def get_config(self) -> dict:
        with self.session_factory() as session:
            config = session.get(ReminderConfig, 1)
            if config is None:
                config = ReminderConfig(
                    id=1,
                    frequency_hours=24,
                    active_window_start="09:00",
                    active_window_end="18:00",
                    escalation_threshold=3,
                    escalation_channel=None,
                    updated_at=self._now(),
                )
                session.add(config)
                session.commit()
                session.refresh(config)
            return {
                "frequency_hours": config.frequency_hours,
                "active_window_start": config.active_window_start,
                "active_window_end": config.active_window_end,
                "escalation_threshold": config.escalation_threshold,
                "escalation_channel": config.escalation_channel,
                "updated_at": config.updated_at,
            }

    def update_config(
        self,
        *,
        actor: User | None = None,
        frequency_hours: int | None = None,
        active_window_start: str | None = None,
        active_window_end: str | None = None,
        escalation_threshold: int | None = None,
        escalation_channel: str | None = None,
    ) -> dict:
        now = self._now()
        with self.session_factory() as session:
            config = session.get(ReminderConfig, 1)
            if config is None:
                config = ReminderConfig(id=1, updated_at=now)
                session.add(config)

            if frequency_hours is not None:
                if frequency_hours <= 0:
                    raise ValueError("frequency_hours must be greater than 0")
                config.frequency_hours = frequency_hours

            if active_window_start is not None:
                try:
                    datetime.strptime(active_window_start, "%H:%M")
                except ValueError:
                    raise ValueError("active_window_start must be HH:MM format")
                config.active_window_start = active_window_start

            if active_window_end is not None:
                try:
                    datetime.strptime(active_window_end, "%H:%M")
                except ValueError:
                    raise ValueError("active_window_end must be HH:MM format")
                config.active_window_end = active_window_end

            if escalation_threshold is not None:
                if escalation_threshold <= 0:
                    raise ValueError("escalation_threshold must be greater than 0")
                config.escalation_threshold = escalation_threshold

            if escalation_channel is not None:
                config.escalation_channel = (
                    normalize_jid(escalation_channel) if escalation_channel else None
                )

            config.updated_at = now
            session.commit()
            session.refresh(config)

            result = {
                "frequency_hours": config.frequency_hours,
                "active_window_start": config.active_window_start,
                "active_window_end": config.active_window_end,
                "escalation_threshold": config.escalation_threshold,
                "escalation_channel": config.escalation_channel,
                "updated_at": config.updated_at,
            }

        if actor:
            audit(
                self.session_factory,
                actor,
                "reminder.config",
                "whatsapp",
                payload={
                    k: v.isoformat() if isinstance(v, datetime) else v
                    for k, v in result.items()
                },
            )

        return result

    def is_within_active_window(self, now: datetime | None = None) -> bool:
        """Check if current UTC time falls within configured active window."""
        cfg = self.get_config()
        if now is None:
            now = self._now()
        cur_time = now.strftime("%H:%M")
        start = cfg["active_window_start"]
        end = cfg["active_window_end"]
        if start <= end:
            return start <= cur_time <= end
        return cur_time >= start or cur_time <= end

    def get_eligible_assignments(
        self, *, force_ignore_window: bool = False, user_jid: str | None = None
    ) -> list[dict]:
        """Find pending/in_progress assignments that require a reminder.

        Enforces frequency window and idempotency: skips assignments that have received
        a reminder within the frequency_hours window or submitted an update within that window.
        """
        now = self._now()
        if not force_ignore_window and not self.is_within_active_window(now):
            return []

        cfg = self.get_config()
        freq_cutoff = now - timedelta(hours=cfg["frequency_hours"])

        wanted_user = normalize_jid(user_jid) if user_jid else None
        with self.session_factory() as session:
            stmt = select(Assignment).where(
                Assignment.status.in_(["pending", "in_progress"])
            )
            rows = session.scalars(stmt).all()

            eligible = []
            for assignment in rows:
                if wanted_user and normalize_jid(assignment.user_jid) != wanted_user:
                    continue
                last_update_at = _as_utc(assignment.last_update_at)
                if last_update_at and last_update_at >= freq_cutoff:
                    continue

                recent_log = session.scalar(
                    select(ReminderLog)
                    .where(
                        ReminderLog.assignment_id == assignment.id,
                        ReminderLog.timestamp >= freq_cutoff,
                        ReminderLog.result.in_(["sent", "escalated"]),
                    )
                    .limit(1)
                )
                if recent_log is not None:
                    continue

                if assignment.target_type == "event":
                    target = session.get(Event, assignment.event_id)
                    if target is None or target.deleted_at is not None:
                        continue
                    if (target.status or "").lower() in CLOSED_EVENT_STATUSES:
                        continue
                    target_name = target.name
                elif assignment.target_type == "task":
                    target = session.get(Task, assignment.task_id)
                    if target is None or target.deleted_at is not None:
                        continue
                    if (target.status or "").lower() in CLOSED_TASK_STATUSES:
                        continue
                    target_name = target.title
                else:
                    continue

                eligible.append({
                    "assignment_id": assignment.id,
                    "target_type": assignment.target_type,
                    "event_id": assignment.event_id,
                    "task_id": assignment.task_id,
                    "event_name": target_name,  # for backward compatibility
                    "user_jid": assignment.user_jid,
                    "status": assignment.status,
                    "reminder_state": assignment.reminder_state,
                    "missed_count": assignment.missed_count,
                    "last_update_at": last_update_at,
                })

            return eligible

    def run_reminders(
        self,
        client: Any,
        actor: User,
        *,
        force_ignore_window: bool = False,
        source: str = "whatsapp",
    ) -> dict:
        """Execute scheduled reminder run idempotently and record attempt logs."""
        now = self._now()
        cfg = self.get_config()
        eligible = self.get_eligible_assignments(force_ignore_window=force_ignore_window)

        results = {
            "eligible": len(eligible),
            "sent": 0,
            "escalated": 0,
            "failed": 0,
        }

        for item in eligible:
            assignment_id = item["assignment_id"]
            user_jid = item["user_jid"]
            target_name = item["event_name"]
            target_type = item.get("target_type", "event")
            target_id = item.get("event_id") if target_type == "event" else item.get("task_id")

            with self.session_factory() as session:
                assignment = session.get(Assignment, assignment_id)
                if not assignment:
                    continue

                new_missed_count = assignment.missed_count + 1
                is_escalated = new_missed_count >= cfg["escalation_threshold"]
                new_state = "escalated" if is_escalated else "sent"

                msg = (
                    f"⏰ *Reminder*: You have a pending assignment for {target_type} *{target_name}* "
                    f"(Assignment #{assignment_id}).\n"
                    f"Please submit your progress update using `!work update {target_type} {target_id} note <value>`."
                )

                sent_ok = False
                err_detail = None
                try:
                    if not client or not hasattr(client, "send_message"):
                        raise RuntimeError("WhatsApp client is unavailable")
                    target_jid_obj = _make_neonize_jid(user_jid)
                    client.send_message(target_jid_obj, msg)
                    sent_ok = True
                except Exception as exc:
                    log.warning("Failed to send reminder to %s: %s", user_jid, exc)
                    err_detail = str(exc)

                if sent_ok:
                    assignment.missed_count = new_missed_count
                    assignment.reminder_state = new_state
                    res_str = "escalated" if is_escalated else "sent"
                    log_entry = ReminderLog(
                        assignment_id=assignment_id,
                        timestamp=now,
                        channel="whatsapp",
                        result=res_str,
                        details=f"Reminder sent to {user_jid} (missed_count={new_missed_count})",
                    )
                    session.add(log_entry)
                    if is_escalated:
                        results["escalated"] += 1
                        esc_target = cfg["escalation_channel"]
                        if esc_target and client and hasattr(client, "send_message"):
                            try:
                                esc_jid_obj = _make_neonize_jid(esc_target)
                                client.send_message(
                                    esc_jid_obj,
                                    f"🚨 *Escalation Alert*: User @{user_jid.split('@')[0]} has missed {new_missed_count} "
                                    f"reminders for {target_type.capitalize()} *{target_name}* (Assignment #{assignment_id}).",
                                )
                            except Exception as esc_err:
                                log.warning("Failed to send escalation alert: %s", esc_err)
                    else:
                        results["sent"] += 1
                else:
                    log_entry = ReminderLog(
                        assignment_id=assignment_id,
                        timestamp=now,
                        channel="whatsapp",
                        result="failed",
                        details=f"Delivery failed: {err_detail}",
                    )
                    session.add(log_entry)
                    results["failed"] += 1

                session.commit()

        audit(
            self.session_factory,
            actor,
            "reminder.run",
            source,
            payload=results,
        )

        return results

    def get_history(
        self,
        assignment_id: int | None = None,
        limit: int = 50,
        *,
        user_jid: str | None = None,
    ) -> list[dict]:
        with self.session_factory() as session:
            stmt = select(ReminderLog).join(Assignment, ReminderLog.assignment_id == Assignment.id)
            if assignment_id is not None:
                stmt = stmt.where(ReminderLog.assignment_id == assignment_id)
            if user_jid is not None:
                stmt = stmt.where(Assignment.user_jid == normalize_jid(user_jid))
            stmt = stmt.order_by(ReminderLog.timestamp.desc()).limit(limit)

            logs = session.scalars(stmt).all()
            return [
                {
                    "id": log_entry.id,
                    "assignment_id": log_entry.assignment_id,
                    "timestamp": log_entry.timestamp,
                    "channel": log_entry.channel,
                    "result": log_entry.result,
                    "details": log_entry.details,
                }
                for log_entry in logs
            ]
