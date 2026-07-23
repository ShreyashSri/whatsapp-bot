from __future__ import annotations

from pbbot.commands.contracts import CommandContext, CommandResult, CommandStatus
from pbbot.commands.parser import CommandParser
from pbbot.commands.registry import CommandRegistry
from pbbot.messages import IncomingWhatsAppMessage


class CommandRouter:
    def __init__(self, parser: CommandParser, registry: CommandRegistry):
        self.parser = parser
        self.registry = registry

    async def route(self, message: IncomingWhatsAppMessage) -> CommandResult:
        if message.from_me:
            return CommandResult(
                status=CommandStatus.IGNORED,
                detected=False,
                message="Ignored an outbound message from this WhatsApp session.",
            )

        command = self.parser.parse(message.body)
        if command is None:
            return CommandResult(
                status=CommandStatus.NOT_COMMAND,
                detected=False,
                message="Message does not contain a command.",
            )

        handler = self.registry.resolve(command.name)
        if handler is None:
            return CommandResult(
                status=CommandStatus.DETECTED,
                detected=True,
                registered=False,
                command=command,
                message="Command detected; no feature is registered for it yet.",
            )

        return await handler(CommandContext(command=command, message=message))
