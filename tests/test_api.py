import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from pbbot.api import create_app
from pbbot.config import Settings


def test_webhook_detects_command() -> None:
    settings = Settings(openwa_webhook_secret="test-secret")
    client = TestClient(create_app(settings))
    body = json.dumps(
        {
            "event": "message.received",
            "sessionId": "main",
            "data": {"id": "message-1", "from": "9199@c.us", "body": "/events"},
        },
        separators=(",", ":"),
    ).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

    response = client.post(
        "/webhooks/openwa",
        content=body,
        headers={"Content-Type": "application/json", "X-OpenWA-Signature": signature},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "detected"
    assert response.json()["command"]["name"] == "events"
    assert response.json()["registered"] is False


def test_webhook_rejects_invalid_signature() -> None:
    settings = Settings(openwa_webhook_secret="test-secret")
    client = TestClient(create_app(settings))

    response = client.post(
        "/webhooks/openwa",
        json={"event": "message.received", "data": {"id": "message-1", "body": "/events"}},
        headers={"X-OpenWA-Signature": "sha256=invalid"},
    )

    assert response.status_code == 401
