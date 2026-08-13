"""JID normalization, authorization, user management, and audit logging."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import func, select

from .models import AuditLog, User

log = logging.getLogger(__name__)
ROLES = {"admin", "member"}


def normalize_jid(value) -> str:
    if value is None:
        return ""
    if getattr(value, "User", "") and getattr(value, "Server", ""):
        user_str = str(value.User).split(":")[0].split(".")[0]
        value = f"{user_str}@{value.Server}"
    value = str(value).strip().lower().replace("whatsapp:", "")
    value = re.sub(r"[:.][0-9]+@", "@", value)
    value = re.sub(r"[^0-9a-z._@-]", "", value)
    if "@" not in value and value:
        value += "@s.whatsapp.net"
    return value


def normalize_group_jid(value) -> str:
    """Normalize a configured group number or group JID to ``@g.us``."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "@" not in raw:
        raw = f"{raw}@g.us"
    normalized = normalize_jid(raw)
    return normalized if normalized.endswith("@g.us") else ""


def jid_user(value) -> str:
    """Return the stable user portion used to bridge phone JIDs and LIDs."""
    return normalize_jid(value).split("@", 1)[0]


def current_user(session_factory: Callable, jid) -> User | None:
    with session_factory() as session:
        normalized = normalize_jid(jid)
        wanted = jid_user(normalized)

        # created LID user does not override the canonical phone user.
        from .work_store import _JID_ALIASES

        canonical = _JID_ALIASES.get(wanted)
        if canonical:
            user = session.get(User, normalize_jid(canonical))
            if user is not None:
                return user

        # Direct JID lookup.
        user = session.get(User, normalized)
        if user is not None:
            return user

        # Fallback: match the user portion directly.
        return next(
            (
                candidate
                for candidate in session.scalars(select(User)).all()
                if jid_user(candidate.jid) == wanted
            ),
            None,
        )


def _clean_push_name(push_name) -> str:
    if not isinstance(push_name, str):
        return ""
    return " ".join(push_name.split()).strip()[:128]


def authorize(
    session_factory,
    actor_jid,
    operation: str,
    required_role: str = "member",
    *,
    push_name: str = "",
) -> User | None:
    actor = normalize_jid(actor_jid)
    if not actor:
        return None
    push_name = _clean_push_name(push_name)
    user = current_user(session_factory, actor)
    if user is None and required_role == "member":
        user = upsert_user(
            session_factory,
            actor,
            role="member",
            display_name=push_name,
            operation="user.auto_create",
        )
    elif user is not None and push_name and user.display_name in {"", jid_user(user.jid)}:
        user = upsert_user(
            session_factory,
            user.jid,
            role=user.role,
            display_name=push_name,
            operation="user.identify",
        )
    allowed = bool(user and user.active and (required_role == "member" or user.role == "admin"))
    log.info("authorization actor=%s operation=%s required_role=%s allowed=%s", actor, operation, required_role, allowed)
    return user if allowed else None


def require_admin(session_factory, actor_jid, operation: str = "unknown") -> User | None:
    return authorize(session_factory, actor_jid, operation, "admin")


def require_member(session_factory, actor_jid, operation: str = "unknown") -> User | None:
    return authorize(session_factory, actor_jid, operation, "member")


# Default denial messages
_DENY_MSG = {
    "admin": "⛔ You need to be an active administrator to use this command.",
    "member": "⛔ An active user account is required.",
}


def gate(
    session_factory,
    sender,
    client,
    chat,
    role: str = "member",
    operation: str = "unknown",
    *,
    push_name: str = "",
):
    """Single-call auth gate for use in feature handlers.

    Returns the authenticated User on success, or None after sending the
    denial reply — so the caller just needs:

        actor = gate(factory, source.Sender, client, chat, "admin", "my.op")
        if not actor: return
    """
    jid = normalize_jid(sender)
    actor = authorize(session_factory, jid, operation, role, push_name=push_name)
    if not actor:
        try:
            client.send_message(chat, _DENY_MSG.get(role, "⛔ Access denied."))
        except Exception as exc:
            log.warning("gate: failed to send denial: %s", exc)
    return actor


def audit(session_factory, actor: User, operation: str, source: str, payload: dict, result: str = "success") -> None:
    with session_factory() as session:
        session.add(AuditLog(actor_jid=actor.jid, actor_role=actor.role, operation=operation,
                             source=source, payload=payload, result=result,
                             timestamp=datetime.now(timezone.utc)))
        session.commit()


def upsert_user(session_factory, jid, role="member", display_name="", *, deactivate=False, actor=None, operation="user.create") -> User:
    jid = normalize_jid(jid)
    if role not in ROLES:
        raise ValueError("role must be admin or member")
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        user = session.get(User, jid)
        if user is None:
            user = User(jid=jid, display_name=display_name or jid.split("@")[0], role=role, active=not deactivate, created_at=now, updated_at=now)
            user.deactivated_at = now if deactivate else None
            session.add(user)
        else:
            if user.active and user.role == "admin" and role != "admin" and active_admin_count(session_factory) <= 1:
                raise ValueError("cannot demote the last active admin")
            if display_name:
                user.display_name = display_name
            user.role = role
            user.active = not deactivate
            if not deactivate:
                user.deactivated_at = None
            user.updated_at = now
        session.commit()
        session.refresh(user)
    if actor:
        audit(session_factory, actor, operation, "whatsapp", {"jid": jid, "role": role})
    return user


def active_admin_count(session_factory) -> int:
    with session_factory() as session:
        return session.scalar(select(func.count()).select_from(User).where(User.active.is_(True), User.role == "admin")) or 0


def get_active_admin_jids(session_factory) -> list[str]:
    with session_factory() as session:
        users = session.query(User).filter(User.role == "admin", User.active.is_(True)).all()
        return [u.jid for u in users]


def set_role(session_factory, jid, role, actor=None) -> User:
    if role not in ROLES:
        raise ValueError("role must be admin or member")
    jid = normalize_jid(jid)
    with session_factory() as session:
        user = session.get(User, jid)
        if not user:
            raise ValueError("user not found")
        if user.active and user.role == "admin" and role != "admin" and active_admin_count(session_factory) <= 1:
            raise ValueError("cannot demote the last active admin")
        user.role, user.updated_at = role, datetime.now(timezone.utc)
        session.commit()
        session.refresh(user)
    if actor:
        audit(session_factory, actor, "user.set_role", "whatsapp", {"jid": jid, "role": role})
    return user


def deactivate_user(session_factory, jid, actor=None) -> User:
    jid = normalize_jid(jid)
    with session_factory() as session:
        user = session.get(User, jid)
        if not user:
            raise ValueError("user not found")
        if user.active and user.role == "admin" and active_admin_count(session_factory) <= 1:
            raise ValueError("cannot deactivate the last active admin")
        now = datetime.now(timezone.utc)
        user.active = False
        user.deactivated_at = now
        user.updated_at = now
        session.commit()
        session.refresh(user)
    if actor:
        audit(session_factory, actor, "user.remove", "whatsapp", {"jid": jid})
    return user


def remove_or_demote_user(session_factory, jid, actor=None) -> tuple[User, str]:
    """If user is admin, demote to member. If user is member, deactivate."""
    jid = normalize_jid(jid)
    with session_factory() as session:
        user = session.get(User, jid)
        if not user:
            raise ValueError(f"user {jid} not found")
        if user.active and user.role == "admin":
            if active_admin_count(session_factory) <= 1:
                raise ValueError("cannot demote the last active admin")
            user.role = "member"
            user.updated_at = datetime.now(timezone.utc)
            action = "demoted to member"
            op = "user.demote"
        else:
            now = datetime.now(timezone.utc)
            user.active = False
            user.deactivated_at = now
            user.updated_at = now
            action = "deactivated"
            op = "user.remove"
        session.commit()
        session.refresh(user)
    if actor:
        audit(session_factory, actor, op, "whatsapp", {"jid": jid, "action": action})
    return user, action