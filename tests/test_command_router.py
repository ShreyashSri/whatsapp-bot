from pbbot.commands.contracts import CommandResult, CommandStatus
from pbbot.commands.parser import CommandParser
from pbbot.commands.registry import CommandRegistry
from pbbot.commands.router import CommandRouter
from pbbot.messages import IncomingWhatsAppMessage


def message(body: str) -> IncomingWhatsAppMessage:
    return IncomingWhatsAppMessage(
        message_id="message-1",
        sender_id="9199@c.us",
        chat_id="9199@c.us",
        body=body,
    )


async def test_detects_unregistered_command_without_executing_a_feature() -> None:
    result = await CommandRouter(CommandParser(), CommandRegistry()).route(message("/events"))

    assert result.status == CommandStatus.DETECTED
    assert result.detected is True
    assert result.registered is False
    assert result.command is not None
    assert result.command.name == "events"


async def test_registry_allows_feature_modules_to_add_a_handler() -> None:
    registry = CommandRegistry()

    async def handler(context):
        return CommandResult(
            status=CommandStatus.HANDLED,
            detected=True,
            registered=True,
            command=context.command,
        )

    registry.register("events", handler)
    result = await CommandRouter(CommandParser(), registry).route(message("/events"))

    assert result.status == CommandStatus.HANDLED
    assert result.registered is True
