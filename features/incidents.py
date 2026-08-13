"""Incident alert webhook feature.

Runs a lightweight Flask HTTP server that accepts Prometheus/Alertmanager-style
payloads on ``POST /alert`` and forwards incident messages to a WhatsApp group.

State is persisted in PostgreSQL so duplicate alerts are suppressed across
restarts.
"""

from __future__ import annotations

import hmac
import logging
import threading
from typing import TYPE_CHECKING

from flask import Flask, request, jsonify

from db.auth import normalize_jid
from db.incident_store import IncidentStore

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state(store: IncidentStore) -> dict:
    return store.read()


def _save_state(store: IncidentStore, state: dict) -> None:
    store.write(state)


def _build_chat_jid(value):
    from neonize.utils import build_jid

    normalized = normalize_jid(value)
    if "@" in normalized:
        user, server = normalized.split("@", 1)
        return build_jid(user, server)
    return build_jid(normalized)


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------


def register(client: "NewClient", config: dict) -> None:
    """Start the incident alert webhook server in a background thread."""
    incident_group_id = config.get("incident_group_id")
    incident_secret = str(config.get("incident_webhook_secret", "") or "")
    incident_port = config.get("incident_port", 8081)

    if not incident_group_id:
        log.warning("INCIDENT_GROUP_ID not set — skipping incident webhook server.")
        return
    if not incident_secret:
        log.error("INCIDENT_WEBHOOK_SECRET not set — skipping incident webhook server.")
        return

    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("Incident feature requires db_session_factory")
    store = IncidentStore(session_factory)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    # Silence Flask's default request logging unless in debug mode
    flask_log = logging.getLogger("werkzeug")
    flask_log.setLevel(logging.WARNING)

    @app.route("/alert", methods=["POST"])
    def alert():
        try:
            supplied_secret = request.headers.get("X-Incident-Webhook-Secret", "")
            if not hmac.compare_digest(supplied_secret, incident_secret):
                return jsonify({"error": "unauthorized"}), 401

            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "request body must be a JSON object"}), 400

            data = payload.get("data")
            if not isinstance(data, list):
                return jsonify({"error": "missing or invalid 'data' array"}), 400

            # Filter for URLs with status code >= 400 or 0 (DNS/connection failure)
            current_failing = []
            for r in data:
                try:
                    url = r["metric"]["instance"]
                    code = round(float(r["value"][1]))
                    if code >= 400 or code == 0:
                        current_failing.append({"url": url, "code": code})
                except (KeyError, IndexError, TypeError, ValueError):
                    continue

            log.info("🔍 Currently failing incident count: %s", len(current_failing))

            active_incidents = _load_state(store)
            current_failing_urls = {f["url"] for f in current_failing}

            new_alerts = []
            resolved_alerts = []

            # Check for NEW incidents
            for item in current_failing:
                url, code = item["url"], item["code"]
                if url not in active_incidents:
                    active_incidents[url] = code
                    new_alerts.append(item)

            # Check for RESOLVED incidents
            for url in list(active_incidents.keys()):
                if url not in current_failing_urls:
                    resolved_alerts.append(url)
                    del active_incidents[url]

            _save_state(store, active_incidents)

            # Send WhatsApp message only if there are changes
            if new_alerts or resolved_alerts:
                parts = []

                for item in new_alerts:
                    url, code = item["url"], item["code"]
                    if code == 0:
                        parts.append(f"{url} DNS/CONNECTION FAILURE 🌐💥\n\nError: {code}\nMessage : HemangBSDK")
                    else:
                        parts.append(f"{url} FAT GAYA 💥\n\nError: {code}\nMessage : HemangBSDK")

                for url in resolved_alerts:
                    parts.append(f"{url} bolne lagi 🚀✨")

                text = "\n".join(parts)

                client.send_message(_build_chat_jid(incident_group_id), text)
                log.info("✅ Sent incident update to WhatsApp.")
            else:
                log.info("💤 No state changes. Suppressing WhatsApp spam.")

            return "", 200

        except Exception as exc:
            log.exception("❌ Incident webhook error: %s", exc)
            return jsonify({"error": "incident webhook failed"}), 500

    def _run_server():
        # Use threaded=False since we're already in a dedicated thread
        app.run(host="0.0.0.0", port=incident_port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    log.info("🚨 Incident webhook listening on :%s/alert", incident_port)
