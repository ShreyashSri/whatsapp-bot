"""Authenticated webhook for Fellowship Tracker opportunity alerts."""
from __future__ import annotations
from datetime import datetime, timezone
import hmac
import logging
import socket
import threading
import time
from typing import TYPE_CHECKING
from flask import Flask, jsonify, request
from sqlalchemy.exc import IntegrityError
from db.models import FellowshipAlert

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)
ALLOWED_EVENTS = frozenset({"new", "reopened"})
MAX_FIELD_LENGTH = 700
STARTUP_TIMEOUT_SECONDS = 5.0

def _clean(value, fallback: str = "Not specified") -> str:
    if value is None:
        return fallback
    text = " ".join(str(value).split()).strip()
    return text[:MAX_FIELD_LENGTH] if text else fallback

def _build_chat_jid(value: str):
    """Build a Neonize JID while keeping the Flask app testable without Neonize."""
    try:
        from neonize.utils import build_jid

        if "@" in value:
            user, server = value.split("@", 1)
            return build_jid(user, server)
        return build_jid(value)
    except Exception:
        return value

def _format_alert(payload: dict) -> str:
    event = str(payload.get("event", "new")).casefold()
    heading = "🆕 New Fellowship Opportunity" if event == "new" else "🔓 Fellowship Reopened"
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    tags_text = ", ".join(
        cleaned for tag in tags if (cleaned := _clean(tag, ""))
    )

    lines = [
        f"*{heading}*",
        "",
        f"*{_clean(payload.get('name'), 'Unnamed opportunity')}*",
        f"Organization: {_clean(payload.get('organization'))}",
        f"Deadline: {_clean(payload.get('deadline'))}",
        f"Stipend: {_clean(payload.get('stipend'))}",
        f"Mode: {_clean(payload.get('mode'))}",
        f"Eligibility: {_clean(payload.get('eligibility'))}",
    ]
    if tags_text:
        lines.append(f"Tags: {_clean(tags_text)}")
    lines.extend([
        "",
        f"Apply: {_clean(payload.get('apply_link'), 'Link unavailable')}",
    ])
    return "\n".join(lines)

def _wait_for_listener(thread: threading.Thread, port: int) -> bool:
    """Wait briefly until the Flask thread has a reachable TCP listener."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not thread.is_alive():
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return thread.is_alive()
        except OSError:
            time.sleep(0.05)
    return False

def create_app(client: "NewClient", config: dict) -> Flask:
    """Create the alert Flask app without starting a listener."""
    group_id = config.get("fellowship_alert_group_id")
    secret = config.get("fellowship_alert_secret", "")
    session_factory = config.get("db_session_factory")
    if not group_id:
        raise RuntimeError("Fellowship alerts require fellowship_alert_group_id")
    if not secret:
        raise RuntimeError("Fellowship alerts require fellowship_alert_secret")
    if session_factory is None:
        raise RuntimeError("Fellowship alerts require db_session_factory")

    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    @app.post("/fellowship-alert")
    def fellowship_alert():
        supplied_secret = request.headers.get("X-Fellowship-Alert-Secret", "")
        if not hmac.compare_digest(supplied_secret, secret):
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        event = str(payload.get("event", "new")).casefold().strip()
        apply_link = str(payload.get("apply_link", "")).strip()
        alert_key = str(payload.get("idempotency_key", "")).strip()
        if event not in ALLOWED_EVENTS:
            return jsonify({"error": "event must be new or reopened"}), 400
        if not apply_link or not alert_key:
            return jsonify({"error": "apply_link and idempotency_key are required"}), 400

        now = datetime.now(timezone.utc)
        try:
            with session_factory.begin() as session:
                if session.get(FellowshipAlert, alert_key) is not None:
                    return jsonify({"status": "duplicate", "idempotency_key": alert_key}), 200
                session.add(
                    FellowshipAlert(
                        alert_key=alert_key,
                        apply_link=apply_link[:MAX_FIELD_LENGTH],
                        event=event,
                        name=_clean(payload.get("name"), "Unnamed opportunity"),
                        sent_at=now,
                    )
                )
        except IntegrityError:
            # Another request may have reserved the same key between the
            # lookup and INSERT. Treat that race as an idempotent duplicate.
            return jsonify({"status": "duplicate", "idempotency_key": alert_key}), 200

        try:
            client.send_message(_build_chat_jid(group_id), _format_alert(payload))
        except Exception:
            # Allow a retry if WhatsApp was unavailable after the reservation was created.
            with session_factory.begin() as session:
                row = session.get(FellowshipAlert, alert_key)
                if row is not None:
                    session.delete(row)
            log.exception("Failed to send fellowship alert to WhatsApp")
            return jsonify({"error": "WhatsApp delivery failed"}), 503

        log.info("Sent fellowship alert event=%s key=%s", event, alert_key)
        return jsonify({"status": "sent", "idempotency_key": alert_key}), 200
    return app

def register(client: "NewClient", config: dict) -> None:
    """Start the webhook listener when fellowship alerts are configured."""
    group_id = config.get("fellowship_alert_group_id")
    secret = config.get("fellowship_alert_secret")
    port = config.get("fellowship_alert_port", 8082)

    log.info(
        "Registering fellowship alert webhook: group=%s port=%s secret_configured=%s",
        group_id or "(not set)",
        port,
        bool(secret),
    )
    if not group_id or not secret:
        log.error(
            "Fellowship alerts disabled: group_configured=%s secret_configured=%s; "
            "set FELLOWSHIP_ALERT_GROUP_ID and FELLOWSHIP_ALERT_SECRET.",
            bool(group_id),
            bool(secret),
        )
        return

    try:
        app = create_app(client, config)
    except Exception:
        log.exception(
            "Fellowship alert webhook could not create its Flask app on port %s",
            port,
        )
        raise

    def _run_server():
        try:
            log.info(
                "Starting fellowship alert Flask server on 0.0.0.0:%s",
                port,
            )
            app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
        except Exception:
            log.exception(
                "Fellowship alert webhook failed while starting or serving on port %s",
                port,
            )
            raise
        finally:
            log.warning(
                "Fellowship alert webhook server stopped on port %s",
                port,
            )

    thread = threading.Thread(
        target=_run_server,
        name="FellowshipAlertWebhook",
        daemon=True,
    )
    thread.start()
    if _wait_for_listener(thread, port):
        log.info("Fellowship alert webhook listening on :%s/fellowship-alert", port)
    else:
        log.error(
            "Fellowship alert webhook did not become reachable on port %s "
            "within %.1f seconds",
            port,
            STARTUP_TIMEOUT_SECONDS,
        )