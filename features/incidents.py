"""Incident alert webhook feature.

Runs a lightweight Flask HTTP server that accepts Prometheus/Alertmanager-style
payloads on ``POST /alert`` and forwards incident messages to a WhatsApp group.

State is persisted in ``incident_state.json`` so duplicate alerts are
suppressed across restarts.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask, request, jsonify

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

_STATE_FILE = Path.cwd() / "incident_state.json"


def _load_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    _STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Feature registration
# ---------------------------------------------------------------------------


def register(client: "NewClient", config: dict) -> None:
    """Start the incident alert webhook server in a background thread."""
    incident_group_id = config.get("incident_group_id")
    incident_port = config.get("incident_port", 8081)

    if not incident_group_id:
        log.warning("INCIDENT_GROUP_ID not set — skipping incident webhook server.")
        return

    app = Flask(__name__)
    # Silence Flask's default request logging unless in debug mode
    flask_log = logging.getLogger("werkzeug")
    flask_log.setLevel(logging.WARNING)

    @app.route("/alert", methods=["POST"])
    def alert():
        try:
            payload = request.get_json(force=True)
            log.info("📦 Incoming incident payload: %s", json.dumps(payload, indent=2))

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

            log.info("🔍 Currently failing: %s", json.dumps(current_failing, indent=2))

            active_incidents = _load_state()
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

            _save_state(active_incidents)

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

                from neonize.utils import build_jid
                jid = build_jid(incident_group_id)
                client.send_message(jid, text)
                log.info("✅ Sent incident update to WhatsApp.")
            else:
                log.info("💤 No state changes. Suppressing WhatsApp spam.")

            return "", 200

        except Exception as exc:
            log.error("❌ Incident webhook error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def _run_server():
        # Use threaded=False since we're already in a dedicated thread
        app.run(host="0.0.0.0", port=incident_port, threaded=True, use_reloader=False)

    thread = threading.Thread(target=_run_server, daemon=True)
    thread.start()
    log.info("🚨 Incident webhook listening on :%s/alert", incident_port)
