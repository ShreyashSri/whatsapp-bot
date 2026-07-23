from __future__ import annotations

from pbbot.commands.contracts import CommandHandler


class CommandRegistry:
    """Registry used by feature modules to contribute commands."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise ValueError("Command name cannot be empty.")
        if normalized in self._handlers:
            raise ValueError(f"Command already registered: {normalized}")
        self._handlers[normalized] = handler

    def resolve(self, name: str) -> CommandHandler | None:
        return self._handlers.get(name.lower())

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
