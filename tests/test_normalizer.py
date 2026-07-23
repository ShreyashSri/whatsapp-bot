from pbbot.whatsapp.normalizer import normalize_openwa_payload


def test_normalizes_openwa_message_envelope() -> None:
    message = normalize_openwa_payload(
        {
            "event": "message.received",
            "sessionId": "main",
            "data": {
                "id": "message-1",
                "from": "120363@g.us",
                "participant": "9199@c.us",
                "body": "/events",
            },
        }
    )

    assert message.message_id == "message-1"
    assert message.session_id == "main"
    assert message.sender_id == "9199@c.us"
    assert message.chat_id == "120363@g.us"
    assert message.body == "/events"
    assert message.is_group is True
