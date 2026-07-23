from typing import Any

from pydantic import BaseModel, Field


class IncomingWhatsAppMessage(BaseModel):
    message_id: str
    session_id: str | None = None
    sender_id: str
    chat_id: str
    body: str = ""
    timestamp: str | int | None = None
    from_me: bool = False
    is_group: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)
