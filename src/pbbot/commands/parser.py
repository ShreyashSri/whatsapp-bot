from __future__ import annotations

import re

from pbbot.commands.contracts import ParsedCommand

COMMAND_NAME = re.compile(r"^[a-z][a-z0-9_-]*$", re.IGNORECASE)


class CommandParser:
    def __init__(self, prefixes: tuple[str, ...] = ("/", "!")):
        self.prefixes = tuple(sorted(prefixes, key=len, reverse=True))

    def parse(self, text: str) -> ParsedCommand | None:
        raw = text.strip()
        prefix = next((candidate for candidate in self.prefixes if raw.startswith(candidate)), None)
        if prefix is None:
            return None

        command_text = raw[len(prefix) :].strip()
        if not command_text:
            return None

        name, *arguments = command_text.split()
        if not COMMAND_NAME.fullmatch(name):
            return None

        return ParsedCommand(
            prefix=prefix,
            name=name.lower(),
            arguments=tuple(arguments),
            raw=raw,
        )
