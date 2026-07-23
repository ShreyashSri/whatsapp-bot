import httpx
import pytest

from pbbot.config import Settings
from pbbot.whatsapp.client import OpenWAClient, _resolve_session_id


class DummyResponse:
    def __init__(self, method: str, url: str, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload
        self._request = httpx.Request(method, url)

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} error",
                request=self._request,
                response=httpx.Response(self.status_code, request=self._request, json=self._payload),
            )


class DummyAsyncClient:
    def __init__(self, responses: list[DummyResponse]):
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    async def __aenter__(self) -> "DummyAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url))
        return self._responses.pop(0)

    async def post(self, url: str, **kwargs):
        self.calls.append(("POST", url))
        return self._responses.pop(0)


def test_resolve_session_id_matches_name_and_id() -> None:
    payload = [
        {"id": "uuid-1", "name": "main"},
        {"id": "uuid-2", "name": "backup"},
    ]

    assert _resolve_session_id(payload, "main") == "uuid-1"
    assert _resolve_session_id(payload, "uuid-2") == "uuid-2"
    assert _resolve_session_id(payload, "missing") is None


@pytest.mark.asyncio
async def test_send_text_resolves_session_and_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyAsyncClient(
        [
            DummyResponse("GET", "http://openwa/api/sessions", 200, [{"id": "uuid-1", "name": "main"}]),
            DummyResponse(
                "POST",
                "http://openwa/api/sessions/uuid-1/messages/send-text",
                200,
                {"id": "wamsg-1", "ack": "sent"},
            ),
        ]
    )

    OpenWAClient._shared_client = None
    monkeypatch.setattr("pbbot.whatsapp.client.httpx.AsyncClient", lambda *args, **kwargs: client)

    settings = Settings(
        openwa_base_url="http://openwa/api",
        openwa_api_key="test-key",
        openwa_session_id="main",
    )
    result = await OpenWAClient(settings).send_text("123@c.us", "hello from pbbot")

    assert result["ack"] == "sent"
    assert client.calls == [
        ("GET", "http://openwa/api/sessions"),
        ("POST", "http://openwa/api/sessions/uuid-1/messages/send-text"),
    ]


@pytest.mark.asyncio
async def test_send_text_without_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    OpenWAClient._shared_client = None
    settings = Settings(openwa_base_url="http://openwa/api", openwa_api_key=None, openwa_session_id="main")

    with pytest.raises(ValueError, match="OPENWA_API_KEY"):
        await OpenWAClient(settings).send_text("123@c.us", "hi")