from types import SimpleNamespace

from bot import _allowed_inbound_chat
from db.transaction import TransactionClient, TransactionDeliveryError
from db.auth import normalize_group_jid
from features.neonize_policy import OutboundDestinationError, install_outbound_policy
from features.text import encode_command_field, public_text, public_url, split_command_fields
import pytest


def test_command_field_codec_round_trips_literal_pipes_and_newlines():
    value = "A | *literal*\nsecond line"
    encoded = encode_command_field(value)
    assert "|" not in encoded
    assert split_command_fields(f"title | {encoded}") == ["title", value.replace("\n", " ")]


def test_public_text_neutralizes_whatsapp_markup_and_line_breaks():
    result = public_text("@all *bold* _under_ ~strike~ `code`\nnext")
    assert result == "＠all ＊bold＊ ＿under＿ ～strike～ ＇code＇ next"


def test_public_url_stays_copyable():
    value = "https://example.test/a_file?q=@person"
    assert public_url(value) == value


def test_card_data_images_are_canonical_base64_only():
    from cards.render import _normalize_data_image

    assert _normalize_data_image("data:image/png;base64,YWJj") == "data:image/png;base64,YWJj"
    with pytest.raises(ValueError):
        _normalize_data_image('data:image/svg+xml,<svg onload="alert(1)"></svg>')


def test_transaction_client_reports_delivery_after_all_replies_are_attempted():
    class Client:
        def __init__(self):
            self.calls = []

        def send_message(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if len(self.calls) == 1:
                raise RuntimeError("transport unavailable")

    client = Client()
    deferred = TransactionClient(client)
    deferred.send_message("chat", "first")
    deferred.send_message("chat", "second")

    with pytest.raises(TransactionDeliveryError):
        deferred.flush_messages()

    assert len(client.calls) == 2


def test_outbound_policy_allows_users_but_rejects_unconfigured_groups():
    class Client:
        def __init__(self):
            self.calls = []

        def send_message(self, destination, text):
            self.calls.append((destination, text))

    client = Client()
    install_outbound_policy(client, {"12345@g.us"})
    client.send_message("99999@s.whatsapp.net", "direct")

    with pytest.raises(OutboundDestinationError):
        client.send_message("54321@g.us", "blocked")

    assert client.calls == [("99999@s.whatsapp.net", "direct")]


def test_group_configuration_normalizes_bare_numbers_consistently():
    assert normalize_group_jid("12345") == "12345@g.us"
    assert normalize_group_jid("12345@g.us") == "12345@g.us"
    assert normalize_group_jid("12345@s.whatsapp.net") == ""


def test_inbound_commands_allow_configured_groups_and_direct_chats():
    config = {"group_ids": {"12345@g.us"}, "pbbot_group_id": None, "media_group_id": None}

    assert _allowed_inbound_chat(config, SimpleNamespace(User="12345", Server="g.us"))
    assert _allowed_inbound_chat(config, SimpleNamespace(User="67890", Server="s.whatsapp.net"))
    assert _allowed_inbound_chat(config, SimpleNamespace(User="67890", Server="lid"))
    assert not _allowed_inbound_chat(config, SimpleNamespace(User="99999", Server="g.us"))
