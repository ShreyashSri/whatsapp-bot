from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from pydantic import BaseModel, Field

from pbbot.messages import IncomingWhatsAppMessage


class CommandStatus(StrEnum):
    NOT_COMMAND = "not_command"
    DETECTED = "detected"
    HANDLED = "handled"
    IGNORED = "ignored"


class ParsedCommand(BaseModel):
    prefix: str
    name: str
    arguments: tuple[str, ...] = ()
    raw: str


class CommandContext(BaseModel):
    command: ParsedCommand
    message: IncomingWhatsAppMessage


class CommandResult(BaseModel):
    status: CommandStatus
    detected: bool
    registered: bool = False
    command: ParsedCommand | None = None
    message: str = ""
    data: dict = Field(default_factory=dict)


CommandHandler = Callable[[CommandContext], Awaitable[CommandResult]]
