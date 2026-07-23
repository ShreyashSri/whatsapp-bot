from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request

from pbbot.commands import CommandParser, CommandRegistry, CommandRouter
from pbbot.config import Settings, get_settings
from pbbot.whatsapp import normalize_openwa_payload, verify_openwa_signature


def create_app(
    settings: Settings | None = None,
    registry: CommandRegistry | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    command_registry = registry or CommandRegistry()
    router = CommandRouter(
        parser=CommandParser(runtime_settings.command_prefixes),
        registry=command_registry,
    )
    application = FastAPI(title="PBBot", version="0.1.0")

    @application.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "pbbot",
            "registered_commands": command_registry.names(),
        }

    @application.post("/webhooks/openwa")
    async def openwa_webhook(
        request: Request,
        x_openwa_signature: str | None = Header(default=None),
    ) -> dict[str, object]:
        body = await request.body()
        if runtime_settings.require_webhook_signature and not verify_openwa_signature(
            body,
            x_openwa_signature,
            runtime_settings.openwa_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="Invalid OpenWA signature.")

        payload = await request.json()
        if payload.get("event") not in {None, "message.received"}:
            return {
                "accepted": True,
                "event": payload.get("event"),
                "status": "ignored",
                "message": "Only message.received events are routed.",
            }

        message = normalize_openwa_payload(payload)
        outcome = await router.route(message)
        return {
            "accepted": True,
            "message_id": message.message_id,
            **outcome.model_dump(mode="json"),
        }

    return application


app = create_app()
