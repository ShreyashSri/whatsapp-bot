from pbbot.commands.parser import CommandParser


def test_detects_slash_command_and_arguments() -> None:
    command = CommandParser().parse("/Tasks list --pending")

    assert command is not None
    assert command.prefix == "/"
    assert command.name == "tasks"
    assert command.arguments == ("list", "--pending")


def test_detects_legacy_bang_command() -> None:
    command = CommandParser().parse(" !stats ")

    assert command is not None
    assert command.prefix == "!"
    assert command.name == "stats"


def test_ignores_regular_messages_and_empty_prefixes() -> None:
    parser = CommandParser()

    assert parser.parse("hello there") is None
    assert parser.parse("/") is None
