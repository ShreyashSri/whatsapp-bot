"""Versioned policy for the installed Neonize client surface.

Neonize exposes a much larger client than the bot should hand to an LLM.
Every public callable is classified here as either an exposed, typed tool
adapter or an intentional exclusion.  The audit is deliberately executable
so a Neonize upgrade cannot silently add an unreviewed capability.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from functools import wraps
import logging
import threading
import time


DESTINATION_METHODS = frozenset({
    "send_message", "send_image", "send_video", "send_audio", "send_document",
    "send_sticker", "send_contact", "reply_message", "pin_message",
    "revoke_message", "set_disappearing_timer", "set_group_announce",
    "set_group_locked", "set_group_name", "set_group_photo", "set_group_topic",
    "update_group_participants", "leave_group", "link_group", "unlink_group",
})


class OutboundDestinationError(RuntimeError):
    """Raised when a destination-bearing Neonize call leaves bot scope."""


_DIRECT_AUTHORIZATION: ContextVar[dict[str, str]] = ContextVar(
    "pbbot_direct_authorization", default={}
)
# Whether the current send is happening inside allow_reminder_delivery().
# Separate from _DIRECT_AUTHORIZATION, which is deliberately DM-only (it
# exists to stop the bot spamming arbitrary phone numbers) -- group sends
# never populate it, so gating reminder-message tracking on it meant a
# reminder delivered to the reminder GROUP was never recorded at all, and a
# reply quoting it could never be recognized.
_REMINDER_SEND_ACTIVE: ContextVar[bool] = ContextVar(
    "pbbot_reminder_send_active", default=False
)
_REMINDER_MESSAGES: dict[str, tuple[str, float]] = {}
_REMINDER_LOCK = threading.Lock()
_REMINDER_TTL_SECONDS = 7 * 24 * 60 * 60
log = logging.getLogger(__name__)


def _jid_text(value) -> str:
    from db.auth import normalize_jid

    user = getattr(value, "User", "")
    server = getattr(value, "Server", "")
    if user and server:
        return normalize_jid(f"{user}@{server}")
    return normalize_jid(value)


def _direct_key(value) -> str:
    jid = _jid_text(value)
    if jid.endswith("@s.whatsapp.net") or jid.endswith("@lid"):
        return jid.split("@", 1)[0]
    return ""


def _reminder_key(value) -> str:
    """Normalized chat identity for reminder-message tracking.

    Unlike ``_direct_key`` this accepts group chats too -- reminder replies
    need to be recognized in the reminder group, not just in DMs.
    """
    return _jid_text(value)


@contextmanager
def _allow_direct(destination, reason: str, *, key_fn=_direct_key):
    key = key_fn(destination)
    if not key:
        yield
        return
    current = _DIRECT_AUTHORIZATION.get()
    token = _DIRECT_AUTHORIZATION.set({**current, key: reason})
    try:
        yield
    finally:
        _DIRECT_AUTHORIZATION.reset(token)


@contextmanager
def allow_reminder_delivery(destination):
    """Allow one explicitly scoped delivery for a reminder, DM or group."""
    with _allow_direct(destination, "reminder"):
        token = _REMINDER_SEND_ACTIVE.set(True)
        try:
            yield
        finally:
            _REMINDER_SEND_ACTIVE.reset(token)


def _quoted_stanza_id(message) -> str:
    """Return the quoted WhatsApp message ID from a message event."""
    root = getattr(message, "Message", message)

    def walk(value, depth: int = 0) -> str:
        if value is None or depth > 8:
            return ""
        context = getattr(value, "contextInfo", None)
        stanza_id = str(getattr(context, "stanzaID", "") or "")
        if stanza_id:
            return stanza_id

        list_fields = getattr(value, "ListFields", None)
        if callable(list_fields):
            try:
                for field, child in value.ListFields():
                    if field.name == "contextInfo":
                        stanza_id = str(getattr(child, "stanzaID", "") or "")
                        if stanza_id:
                            return stanza_id
                    if getattr(field, "message_type", None):
                        stanza_id = walk(child, depth + 1)
                        if stanza_id:
                            return stanza_id
            except Exception:
                return ""

        for name in (
            "extendedTextMessage", "imageMessage", "videoMessage", "audioMessage",
            "documentMessage", "stickerMessage", "ephemeralMessage", "viewOnceMessage",
            "viewOnceMessageV2", "editedMessage", "message",
        ):
            child = getattr(value, name, None)
            if child is not None and child is not value:
                stanza_id = walk(child, depth + 1)
                if stanza_id:
                    return stanza_id
        return ""

    return walk(root)


def _message_chat(message):
    source = getattr(getattr(message, "Info", None), "MessageSource", None)
    return getattr(source, "Chat", None)


def _record_reminder_message(response, destination) -> None:
    message_id = getattr(response, "ID", "") if response is not None else ""
    if not isinstance(message_id, str) or not message_id:
        return
    key = _reminder_key(destination)
    if not key:
        return
    expires_at = time.monotonic() + _REMINDER_TTL_SECONDS
    with _REMINDER_LOCK:
        now = time.monotonic()
        for stale_id, (_, expiry) in list(_REMINDER_MESSAGES.items()):
            if expiry <= now:
                _REMINDER_MESSAGES.pop(stale_id, None)
        _REMINDER_MESSAGES[message_id] = (key, expires_at)


def reminder_reply_destination(message) -> str:
    """Return the chat (DM or reminder group) when a message quotes a live
    bot reminder."""
    stanza_id = _quoted_stanza_id(message)
    chat = _message_chat(message)
    key = _reminder_key(chat)
    if not stanza_id or not key:
        return ""
    with _REMINDER_LOCK:
        reminder = _REMINDER_MESSAGES.get(stanza_id)
        if reminder is None:
            return ""
        reminder_key, expires_at = reminder
        if expires_at <= time.monotonic():
            _REMINDER_MESSAGES.pop(stanza_id, None)
            return ""
        if reminder_key != key:
            return ""
    return _jid_text(chat)


def is_reminder_reply(message) -> bool:
    return bool(reminder_reply_destination(message))


@contextmanager
def allow_reminder_reply(message):
    """Allow replies only to the DM that quoted a tracked bot reminder."""
    destination = reminder_reply_destination(message)
    with _allow_direct(destination, "reminder_reply") if destination else nullcontext():
        yield


@contextmanager
def allow_reply_to_source_chat(message):
    """Allow outbound sends back to the exact chat an inbound message came from.

    Inbound handling accepts commands from any group the bot has joined
    (see bot.py's _allowed_inbound_chat) -- a command's reply must be able
    to reach that same group even when it isn't in the static configured
    group list, or every command silently "does nothing" in any group that
    wasn't manually added to GROUP_ID/MEDIA_GROUP_ID/etc. Replying to the
    chat a request already came from carries no more risk than accepting
    that request did; this does not authorize any OTHER destination an NL
    plan might try to construct.
    """
    chat = _message_chat(message)
    with _allow_direct(chat, "source_chat", key_fn=_reminder_key) if chat else nullcontext():
        yield


def _allowed_destination(value, allowed_groups: set[str]) -> bool:
    jid = _jid_text(value)
    if jid.endswith("@s.whatsapp.net") or jid.endswith("@lid"):
        return _direct_key(jid) in _DIRECT_AUTHORIZATION.get()
    return jid.endswith("@g.us") and (jid in allowed_groups or jid in _DIRECT_AUTHORIZATION.get())


def _destinations(method: str, args: tuple, kwargs: dict) -> list[object]:
    if method in {"link_group", "unlink_group"}:
        return [
            args[0] if args else (kwargs.get("parent") or kwargs.get("parent_chat")),
            args[1] if len(args) > 1 else (kwargs.get("child") or kwargs.get("child_chat")),
        ]
    if method == "reply_message":
        # Neonize's reply_message(message, quoted, to=None, ..., reply_privately=False, ...)
        # can redirect the actual send away from the quoted message's chat via
        # either `to` or `reply_privately`. Validate whichever destination the
        # call will really use, not just the quoted message's chat -- otherwise
        # a caller could pass to=<arbitrary jid> or reply_privately=True and
        # have the guard check the wrong chat while the real send goes
        # elsewhere.
        quoted = kwargs.get("quoted")
        if quoted is None and len(args) > 1:
            quoted = args[1]
        source = getattr(getattr(quoted, "Info", None), "MessageSource", None)
        to = kwargs.get("to", args[2] if len(args) > 2 else None)
        if to is not None:
            return [to]
        reply_privately = kwargs.get("reply_privately", args[4] if len(args) > 4 else False)
        if reply_privately:
            return [getattr(source, "Sender", None)]
        return [getattr(source, "Chat", None)]
    if args:
        return [args[0]]
    for key in ("chat", "chat_jid", "jid", "parent_chat", "group_jid"):
        if key in kwargs:
            return [kwargs[key]]
    return []


def install_outbound_policy(client, allowed_groups) -> None:
    """Guard all exposed destination-bearing methods on one live client.

    Direct-user delivery is blocked unless an explicit reminder or reminder
    reply context is active. Group delivery is restricted to configured bot
    groups. The wrapper raises a typed error so a blocked side effect cannot
    be reported as successful by a caller that ignores a ``None`` return value.
    """
    from db.auth import normalize_jid

    groups = {normalize_jid(value) for value in (allowed_groups or set()) if normalize_jid(value)}
    if getattr(client, "_pbbot_outbound_policy_installed", False):
        return

    for method in DESTINATION_METHODS:
        original = getattr(client, method, None)
        if not callable(original):
            continue

        @wraps(original)
        def guarded(*args, __method=method, __original=original, **kwargs):
            destinations = _destinations(__method, args, kwargs)
            if not destinations or any(
                not _allowed_destination(destination, groups)
                for destination in destinations
            ):
                rendered = ", ".join(_jid_text(item) for item in destinations if item) or "(unknown)"
                raise OutboundDestinationError(
                    f"outbound {__method} destination is outside configured bot scope: {rendered}"
                )
            response = __original(*args, **kwargs)
            if __method in {"send_message", "reply_message"}:
                message_id = str(getattr(response, "ID", "") or "")
                log.info(
                    "outbound WhatsApp %s delivered destination=%s message_id=%s",
                    __method,
                    ",".join(_jid_text(destination) for destination in destinations if destination),
                    message_id or "(unknown)",
                )
            if __method == "send_message" and _REMINDER_SEND_ACTIVE.get():
                for destination in destinations:
                    _record_reminder_message(response, destination)
            return response

        setattr(client, method, guarded)
    client._pbbot_outbound_policy_installed = True

EXPOSED_NEONIZE_METHODS = frozenset({
    "build_document_message", "build_image_message", "build_poll_vote_creation",
    "build_reaction", "create_group", "download_any", "get_blocklist",
    "get_contact_qr_link", "get_group_info", "get_group_info_from_link", "get_group_invite_link", "get_me",
    "get_group_request_participants", "get_joined_groups", "get_lid_from_pn",
    "get_linked_group_participants", "get_pn_from_lid", "get_profile_picture",
    "get_sub_groups", "get_user_devices", "get_user_info", "is_on_whatsapp",
    "join_group_with_link", "leave_group", "pin_message", "reply_message",
    "revoke_message", "send_audio", "send_contact", "send_document",
    "send_image", "send_message", "send_sticker", "send_video",
    "set_disappearing_timer", "set_group_announce", "set_group_locked",
    "set_group_name", "set_group_photo", "set_group_topic",
    "update_blocklist", "update_group_participants", "link_group", "unlink_group",
    "set_profile_name", "set_status_message", "set_profile_photo",
})


_EXCLUDED_GROUPS = {
    "session and transport operations": (
        "PairPhone", "connect", "connect_with_proxy", "disconnect", "logout",
        "stop", "set_passive", "set_proxy_address", "set_force_activate_delivery_receipts",
        "send_app_state", "generate_message_id",
    ),
    "unsupported or untrusted media/protocol primitives": (
        "build_album_content", "build_audio_message", "build_poll_vote", "build_reply_message",
        "build_revoke", "build_sticker_message", "build_stickerpack_message", "build_video_message",
        "download_media_with_path", "decrypt_poll_vote", "edit_message", "get_message_for_retry",
        "send_album", "send_fb_message", "send_interactive_message", "send_presence", "send_stickerpack",
        "send_chat_presence", "upload",
    ),
    "newsletter surface outside this bot's group/task domain": (
        "create_newsletter", "follow_newsletter", "get_newsletter_info",
        "get_newsletter_info_with_invite", "get_newsletter_message_update",
        "get_newsletter_messages", "get_subscribed_newletters", "newsletter_mark_viewed",
        "newsletter_send_reaction", "newsletter_subscribe_live_updates",
        "newsletter_toggle_mute", "unfollow_newsletter", "upload_newsletter",
    ),
    "global account/privacy surface requires a separate explicit admin subsystem": (
        "get_privacy_settings", "get_status_privacy",
        "resolve_business_message_link", "resolve_contact_qr_link", "set_default_disappearing_timer",
        "set_privacy_setting",
        "subscribe_presence", "mark_read",
    ),
    "cross-group or protocol-link operations require explicit entity lifecycle design": (
        "get_group_info_from_invite", "join_group_with_invite",
    ),
}

EXCLUDED_NEONIZE_METHODS = {
    method: reason
    for reason, methods in _EXCLUDED_GROUPS.items()
    for method in methods
}


def audit_neonize_surface(client_type=None) -> dict[str, list[str]]:
    """Return unknown and stale policy entries for a Neonize client class."""
    if client_type is None:
        from neonize.client import NewClient

        client_type = NewClient
    public = set()
    for name in dir(client_type):
        if name.startswith("_"):
            continue
        try:
            if callable(getattr(client_type, name)):
                public.add(name)
        except Exception:
            continue
    classified = set(EXPOSED_NEONIZE_METHODS) | set(EXCLUDED_NEONIZE_METHODS)
    return {
        "unclassified": sorted(public - classified),
        "stale_exposed": sorted(EXPOSED_NEONIZE_METHODS - public),
        "stale_excluded": sorted(set(EXCLUDED_NEONIZE_METHODS) - public),
        "conflicts": sorted(set(EXPOSED_NEONIZE_METHODS) & set(EXCLUDED_NEONIZE_METHODS)),
    }


def policy_reason(method: str) -> str:
    """Explain why a method is or is not available to the planner."""
    if method in EXPOSED_NEONIZE_METHODS:
        return "exposed through a typed, authorized agent tool"
    return EXCLUDED_NEONIZE_METHODS.get(method, "not classified")
