"""Small, shared codecs for user-controlled WhatsApp text.

The bot uses ``|`` as a command-field separator. Natural-language compilation
must therefore encode literal separators before building a command, and every
active parser must decode them again.
"""

from __future__ import annotations


def encode_command_field(value: object) -> str:
    """Encode one field without changing its visible meaning."""
    text = str(value or "")
    return (
        text.replace("¦", "¦¦")
        .replace("|", "¦p")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def decode_command_field(value: object) -> str:
    text = str(value or "")
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "¦" or index + 1 >= len(text):
            output.append(text[index])
            index += 1
            continue
        marker = text[index + 1]
        if marker == "p":
            output.append("|")
            index += 2
        elif marker == "¦":
            output.append("¦")
            index += 2
        else:
            output.append("¦")
            index += 1
    return "".join(output)


def split_command_fields(value: object, *, limit: int = -1) -> list[str]:
    """Split raw command fields while preserving encoded literal pipes."""
    text = str(value or "")
    fields: list[str] = []
    current: list[str] = []
    splits = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "¦" and index + 1 < len(text) and text[index + 1] in {"p", "¦"}:
            current.extend((char, text[index + 1]))
            index += 2
            continue
        if char == "|" and (limit < 0 or splits < limit):
            fields.append(decode_command_field("".join(current)).strip())
            current = []
            splits += 1
            index += 1
            continue
        current.append(char)
        index += 1
    fields.append(decode_command_field("".join(current)).strip())
    return fields


def public_text(value: object, *, limit: int | None = None) -> str:
    """Make user-controlled values safe for WhatsApp's lightweight markup."""
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    # WhatsApp formatting markers are not data. Full-width equivalents keep
    # the visible character while preventing user text from changing layout.
    text = text.translate(str.maketrans({
        "*": "＊", "_": "＿", "~": "～", "`": "＇", "@": "＠",
    }))
    return text[:limit] if limit is not None else text


def public_url(value: object, *, limit: int | None = None) -> str:
    """Keep a displayable URL copyable without markup rewriting."""
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return text[:limit] if limit is not None else text


def public_error(error: BaseException, fallback: str) -> str:
    """Keep short, deliberate validation messages while hiding internals."""
    text = public_text(error, limit=200)
    lowered = text.casefold()
    unsafe = ("traceback", "sql", "constraint", "sqlite", "postgres", "database", "object at 0x")
    safe_prefixes = (
        "usage:", "must ", "requires ", "please ", "only ", "cannot ",
        "could not ", "not found", "no ", "invalid ", "mention ",
        "dates ", "priority ", "status ", "field ", "target ",
    )
    safe_fragments = (
        "is not a field on this event",
        "must be one of the allowed values",
    )
    if text and len(text) <= 200 and not any(marker in lowered for marker in unsafe):
        if lowered.startswith(safe_prefixes) or any(fragment in lowered for fragment in safe_fragments):
            return text
    return fallback
