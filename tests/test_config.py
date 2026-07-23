from pbbot.config import Settings


def test_parses_comma_separated_command_prefixes(monkeypatch) -> None:
    monkeypatch.setenv("COMMAND_PREFIXES", "/,!")

    assert Settings().command_prefixes == ("/", "!")
