from __future__ import annotations

from typing import Any
from uuid import uuid4

from pbbot.messages import IncomingWhatsAppMessage


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _first_text(*values: Any) -> str:
    return next((value for value in values if isinstance(value, str)), "")


def _message_text(container: dict[str, Any]) -> str:
    nested = container.get("message") if isinstance(container.get("message"), dict) else {}
    extended = nested.get("extendedTextMessage") if isinstance(nested.get("extendedTextMessage"), dict) else {}
    image = nested.get("imageMessage") if isinstance(nested.get("imageMessage"), dict) else {}
    video = nested.get("videoMessage") if isinstance(nested.get("videoMessage"), dict) else {}
    document = nested.get("documentMessage") if isinstance(nested.get("documentMessage"), dict) else {}
    return _first_text(
        container.get("body"),
        container.get("text"),
        container.get("caption"),
        container.get("message"),
        nested.get("conversation"),
        extended.get("text"),
        image.get("caption"),
        video.get("caption"),
        document.get("caption"),
    )


def normalize_openwa_payload(payload: dict[str, Any]) -> IncomingWhatsAppMessage:
    """Normalize OpenWA payload variants before they reach command routing.

    This mirrors media_automata's gateway boundary so feature modules never
    depend on OpenWA's raw webhook nesting.
    """
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raw_data = payload.get("payload")
    data = raw_data if isinstance(raw_data, dict) else payload

    message_id = _first(data.get("id"), data.get("messageId"), data.get("message_id"), payload.get("id"))
    from_me = bool(data.get("fromMe", False))
    chat_id = _first(
        data.get("chatId"),
        data.get("chat_id"),
        data.get("from") if not from_me else None,
        data.get("to"),
        "unknown",
    )
    sender_id = _first(
        data.get("participant"),
        data.get("author"),
        data.get("fromNumber"),
        data.get("sender"),
        data.get("from"),
        chat_id,
        "unknown",
    )

    return IncomingWhatsAppMessage(
        message_id=str(message_id or f"wamsg_{uuid4().hex}"),
        session_id=str(_first(payload.get("sessionId"), data.get("sessionId"))) if _first(
            payload.get("sessionId"), data.get("sessionId")
        ) else None,
        sender_id=str(sender_id),
        chat_id=str(chat_id),
        body=_message_text(data),
        timestamp=_first(data.get("timestamp"), data.get("waTimestamp")),
        from_me=from_me,
        is_group=bool(data.get("isGroup", str(chat_id).endswith("@g.us"))),
        raw=payload,
    )
