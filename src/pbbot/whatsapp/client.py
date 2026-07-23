from typing import Any, Protocol
from uuid import uuid4

import httpx

from pbbot.config import Settings


class WhatsAppClient(Protocol):
    async def send_text(self, chat_id: str, text: str) -> dict:
        ...

    async def get_session(self) -> dict:
        ...

    async def start_session(self) -> dict:
        ...


class OpenWAClient:
    _shared_client: httpx.AsyncClient | None = None

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.openwa_base_url.rstrip("/")
        self._resolved_session_id: str | None = None

    @classmethod
    def get_client(cls) -> httpx.AsyncClient:
        if cls._shared_client is None or getattr(cls._shared_client, "is_closed", False):
            cls._shared_client = httpx.AsyncClient()
        return cls._shared_client

    async def send_text(self, chat_id: str, text: str) -> dict:
        return await self._post_message("send-text", {"chatId": chat_id, "text": text})

    async def _post_message(self, endpoint: str, payload: dict) -> dict:
        client = self.get_client()
        session_id = await self._session_id(client)
        url = f"{self.base_url}/sessions/{session_id}/messages/{endpoint}"
        headers = self._headers(include_content_type=True)
        body = {key: value for key, value in payload.items() if value}
        response = await client.post(url, headers=headers, json=body, timeout=20.0)
        response.raise_for_status()
        return response.json()

    async def get_session(self) -> dict:
        client = self.get_client()
        session_id = await self._session_id(client)
        url = f"{self.base_url}/sessions/{session_id}"
        headers = self._headers()
        response = await client.get(url, headers=headers, timeout=20.0)
        response.raise_for_status()
        return response.json()

    async def start_session(self) -> dict:
        client = self.get_client()
        session_id = await self._session_id(client)
        url = f"{self.base_url}/sessions/{session_id}/start"
        headers = self._headers()
        response = await client.post(url, headers=headers, json={}, timeout=60.0)
        if response.status_code in {400, 409}:
            current = await client.get(
                f"{self.base_url}/sessions/{session_id}",
                headers=headers,
                timeout=20.0,
            )
            current.raise_for_status()
            return current.json()
        response.raise_for_status()
        return response.json()

    async def _session_id(self, client: httpx.AsyncClient) -> str:
        if self._resolved_session_id:
            return self._resolved_session_id

        session_ref = self.settings.openwa_session_id.strip()
        if not session_ref:
            raise ValueError("OPENWA_SESSION_ID is required.")

        response = await client.get(f"{self.base_url}/sessions", headers=self._headers())
        response.raise_for_status()
        resolved_session_id = _resolve_session_id(response.json(), session_ref)
        if resolved_session_id and resolved_session_id != session_ref:
            self._resolved_session_id = resolved_session_id
            return resolved_session_id
        return session_ref

    def _headers(self, *, include_content_type: bool = False) -> dict[str, str]:
        if not self.settings.openwa_api_key:
            raise ValueError("OPENWA_API_KEY is required.")
        headers = {
            "X-API-Key": self.settings.openwa_api_key,
            "X-Request-ID": f"req_{uuid4().hex}",
        }
        if include_content_type:
            headers["Content-Type"] = "application/json"
        return headers


def build_whatsapp_client(settings: Settings) -> WhatsAppClient:
    return OpenWAClient(settings)


def _resolve_session_id(payload: Any, session_ref: str) -> str | None:
    for item in _iter_sessions(payload):
        item_id = str(item.get("id") or "").strip()
        item_name = str(item.get("name") or "").strip()
        if session_ref == item_id or (item_name and session_ref == item_name):
            return item_id or None
    return None


def _iter_sessions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("sessions", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []
