"""SIH 2026 problem-statement watcher.

The Mumbai scraper POSTs JSON to POST /sih-ingest. This process does not
fetch sih.gov.in. !sih refresh POSTs to the scraper trigger instead.

Chat:
  !sih              summary + hottest PS
  !sih hot          every PS at or over the threshold
  !sih top [n]      top n by submissions (default 10, max 25)
  !sih <ps-number>  one problem statement
  !sih refresh      POST the Mumbai scraper /scrape (not this host)
  !sih add dsce [..]     add PS numbers to the DSCE watchlist
  !sih remove dsce [..]  remove PS numbers from the DSCE watchlist
  !sih dsce top [n]      top n from the DSCE watchlist (default 10)
"""

from __future__ import annotations

import hmac
import logging
import socket
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

import httpx
from flask import Flask, jsonify, request

import re

from db.auth import gate
from db.sih_store import SIHStore
from features.subgroups import _get_text
from features.text import public_text

PS_NUMBER_RE = re.compile(r"^SIH\d+$", re.I)

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 300
DEFAULT_INGEST_PORT = 8083
WHATSAPP_LIST_LIMIT = 20
STARTUP_TIMEOUT_SECONDS = 5.0
MAX_ROWS = 5000

SIH_MODULE_HELP = (
    "*SIH 2026 problem statements*\n\n"
    "`!sih` — summary and hottest PS.\n"
    "`!sih hot` — every PS at or over 300 submissions.\n"
    "`!sih top [n]` — top n by submissions (default 10, max 25).\n"
    "`!sih <ps-number>` — one problem statement, e.g. `!sih SIH1601`.\n"
    "`!sih refresh` — ask the India scraper to fetch and POST counts here.\n"
    "`!sih add dsce 26001, 26002` — track those PS on the DSCE list.\n"
    "`!sih remove dsce 26001` — drop PS from the DSCE list.\n"
    "`!sih dsce top [n]` — hottest tracked DSCE PS (default 10).\n\n"
    "The bot alerts the SIH group the first time a PS crosses 300 submissions."
)

DSCE_LIST = "dsce"
WATCHLIST_ADD_LIMIT = 50


def normalize_ps_number(token: str) -> str | None:
    raw = (token or "").strip().upper().strip("[],")
    if not raw:
        return None
    if raw.isdigit():
        raw = f"SIH{raw}"
    if not PS_NUMBER_RE.match(raw):
        return None
    return raw


def parse_ps_list(text: str) -> list[str]:
    cleaned = (text or "").replace("[", " ").replace("]", " ").replace(",", " ")
    seen: set[str] = set()
    ordered: list[str] = []
    for token in cleaned.split():
        ps_number = normalize_ps_number(token)
        if not ps_number or ps_number in seen:
            continue
        seen.add(ps_number)
        ordered.append(ps_number)
    return ordered


def _build_chat_jid(value: str):
    from neonize.utils import build_jid
    from db.auth import normalize_group_jid

    normalized = normalize_group_jid(value)
    if not normalized:
        raise ValueError("SIH alert group must be a WhatsApp group JID")
    user, server = normalized.split("@", 1)
    return build_jid(user, server)


def _format_threshold_alert(row: dict[str, Any], threshold: int) -> str:
    title = public_text(row.get("title") or "Untitled", limit=160)
    org = public_text(row.get("organization") or "Unknown org", limit=80)
    theme = public_text(row.get("theme") or "-", limit=60)
    category = public_text(row.get("category") or "-", limit=40)
    ps_number = public_text(row.get("ps_number") or "?", limit=40)
    count = int(row.get("submitted_ideas_count") or 0)
    limit = row.get("submitted_ideas_limit")
    count_text = f"{count}/{limit}" if limit not in (None, "") else str(count)
    deadline = public_text(row.get("deadline") or "-", limit=40)
    return (
        "*SIH 2026 PS crossed 300 submissions*\n\n"
        f"*{ps_number}*\n"
        f"{title}\n"
        f"Organization: {org}\n"
        f"Theme: {theme}\n"
        f"Category: {category}\n"
        f"Submissions: {count_text} (threshold {threshold})\n"
        f"Deadline: {deadline}\n"
        f"https://sih.gov.in/sih2026PS"
    )


def _format_ps_line(row: dict[str, Any]) -> str:
    ps_number = public_text(row.get("ps_number") or "?", limit=32)
    count = int(row.get("submitted_ideas_count") or 0)
    limit = row.get("submitted_ideas_limit")
    count_text = f"{count}/{limit}" if limit not in (None, "") else str(count)
    title = public_text(row.get("title") or "Untitled", limit=70)
    return f"• `{ps_number}` — {count_text} — {title}"


def _format_summary(rows: list[dict[str, Any]], threshold: int) -> str:
    hot = [row for row in rows if int(row.get("submitted_ideas_count") or 0) >= threshold]
    top = rows[:10]
    lines = [
        "*SIH 2026 problem statements*",
        "",
        f"Tracked: {len(rows)}",
        f"At or over {threshold}: {len(hot)}",
        "",
        "*Hottest*",
    ]
    if not top:
        lines.append("No problem statements stored yet. Wait for the next scrape, or `!sih refresh`.")
    else:
        lines.extend(_format_ps_line(row) for row in top)
        lines.extend(["", "Use `!sih hot`, `!sih top 15`, or `!sih SIH1601`."])
    return "\n".join(lines)


def _format_hot(rows: list[dict[str, Any]], threshold: int) -> str:
    if not rows:
        return f"No SIH 2026 PS has reached {threshold} submissions yet."
    shown = rows[:WHATSAPP_LIST_LIMIT]
    extra = len(rows) - len(shown)
    lines = [f"*SIH 2026 PS at or over {threshold}* ({len(rows)})", ""]
    lines.extend(_format_ps_line(row) for row in shown)
    if extra > 0:
        lines.append(f"\n…and {extra} more. Use `!sih top 25` for a shorter ranked list.")
    return "\n".join(lines)


def _format_one(row: dict[str, Any], threshold: int) -> str:
    count = int(row.get("submitted_ideas_count") or 0)
    over = "YES" if count >= threshold else "no"
    return "\n".join(
        [
            f"*{public_text(row.get('ps_number'), limit=40)}*",
            public_text(row.get("title") or "Untitled", limit=200),
            "",
            f"Organization: {public_text(row.get('organization') or '-', limit=100)}",
            f"Theme: {public_text(row.get('theme') or '-', limit=80)}",
            f"Category: {public_text(row.get('category') or '-', limit=40)}",
            f"Submissions: {count}"
            + (f"/{row['submitted_ideas_limit']}" if row.get("submitted_ideas_limit") not in (None, "") else ""),
            f"Deadline: {public_text(row.get('deadline') or '-', limit=40)}",
            f"Over {threshold}: {over}",
        ]
    )


def ingest_rows(
    store: SIHStore,
    rows: list[dict[str, Any]],
    source_url: str,
    client: "NewClient | None",
    group_id: str | None,
    threshold: int,
    *,
    notify: bool = True,
) -> dict[str, Any]:
    store.upsert(rows)
    over = [row for row in rows if int(row.get("submitted_ideas_count") or 0) >= threshold]
    store.record_snapshot(source_url, len(rows), len(over))

    pending = store.pending_alerts(threshold)
    sent = 0
    if notify and client is not None and group_id and pending:
        chat = _build_chat_jid(group_id)
        for row in pending:
            try:
                client.send_message(chat, _format_threshold_alert(row, threshold))
            except Exception:
                log.exception("Failed to send SIH alert for %s", row.get("ps_number"))
                continue
            store.mark_alerted(
                row["ps_number"],
                row.get("title") or "",
                int(row["submitted_ideas_count"]),
                threshold,
            )
            sent += 1
            log.info(
                "SIH alerted %s at %s submissions",
                row["ps_number"],
                row["submitted_ideas_count"],
            )
    return {
        "total": len(rows),
        "over_threshold": len(over),
        "alerts_sent": sent,
        "source_url": source_url,
    }


def trigger_remote_scrape(trigger_url: str, secret: str) -> dict[str, Any]:
    url = trigger_url.rstrip("/")
    if not url.endswith("/scrape"):
        url = url + "/scrape"
    response = httpx.post(
        url,
        headers={"X-SIH-Alert-Secret": secret, "Content-Type": "application/json"},
        timeout=180.0,
        follow_redirects=True,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"scraper HTTP {response.status_code}: {response.text[:300]}")
    try:
        body = response.json()
    except Exception:
        body = {"status": "ok"}
    if not isinstance(body, dict):
        body = {"status": "ok"}
    return body


def _wait_for_listener(thread: threading.Thread, port: int) -> bool:
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


def create_ingest_app(
    client: "NewClient",
    store: SIHStore,
    group_id: str | None,
    secret: str,
    threshold: int,
) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    lock = threading.Lock()

    @app.post("/sih-ingest")
    def sih_ingest():
        supplied = request.headers.get("X-SIH-Alert-Secret", "")
        if not hmac.compare_digest(supplied, secret):
            return jsonify({"error": "unauthorized"}), 401

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        rows = payload.get("problem_statements")
        if not isinstance(rows, list) or not rows:
            return jsonify({"error": "problem_statements must be a non-empty array"}), 400
        if len(rows) > MAX_ROWS:
            return jsonify({"error": f"problem_statements exceeds {MAX_ROWS} rows"}), 400

        cleaned: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            ps_number = str(item.get("ps_number") or "").strip()
            if not ps_number:
                continue
            try:
                count = int(item.get("submitted_ideas_count") or 0)
            except (TypeError, ValueError):
                count = 0
            try:
                raw_limit = item.get("submitted_ideas_limit")
                limit = int(raw_limit) if raw_limit not in (None, "") else None
            except (TypeError, ValueError):
                limit = None
            cleaned.append(
                {
                    "ps_number": ps_number[:64],
                    "serial_number": str(item.get("serial_number") or "")[:32],
                    "organization": str(item.get("organization") or "")[:500],
                    "title": str(item.get("title") or "")[:500],
                    "category": str(item.get("category") or "")[:64],
                    "theme": str(item.get("theme") or "")[:128],
                    "submitted_ideas_count": max(0, count),
                    "submitted_ideas_limit": limit,
                    "deadline": str(item.get("deadline") or "")[:64],
                }
            )
        if not cleaned:
            return jsonify({"error": "no valid problem statements"}), 400

        source_url = str(payload.get("source_url") or "")[:500]
        with lock:
            result = ingest_rows(store, cleaned, source_url, client, group_id, threshold)
        log.info(
            "SIH ingest total=%s over=%s alerts=%s",
            result["total"],
            result["over_threshold"],
            result["alerts_sent"],
        )
        return jsonify({"status": "ok", **result}), 200

    @app.get("/sih-health")
    def sih_health():
        return jsonify({"status": "ok"}), 200

    return app


def _start_ingest_server(app: Flask, port: int) -> None:
    def _run() -> None:
        try:
            log.info("Starting SIH ingest Flask server on 0.0.0.0:%s", port)
            app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)
        except Exception:
            log.exception("SIH ingest webhook failed on port %s", port)
            raise

    thread = threading.Thread(target=_run, name="SIHIngestWebhook", daemon=True)
    thread.start()
    if _wait_for_listener(thread, port):
        log.info("SIH ingest webhook listening on :%s/sih-ingest", port)
    else:
        log.error("SIH ingest webhook did not become reachable on port %s", port)


def register(client: "NewClient", config: dict) -> Callable:
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        raise RuntimeError("SIH feature requires db_session_factory")

    store = SIHStore(session_factory)
    group_id = (config.get("sih_group_id") or "").strip() or None
    threshold = int(config.get("sih_threshold") or DEFAULT_THRESHOLD)
    secret = (config.get("sih_ingest_secret") or "").strip()
    ingest_port = int(config.get("sih_ingest_port") or DEFAULT_INGEST_PORT)
    scraper_url = (config.get("sih_scraper_url") or "").strip()
    scraper_secret = (config.get("sih_scraper_secret") or "").strip() or secret

    if secret:
        app = create_ingest_app(client, store, group_id, secret, threshold)
        _start_ingest_server(app, ingest_port)
    else:
        log.warning(
            "SIH ingest webhook disabled: set SIH_INGEST_SECRET "
            "(and expose SIH_INGEST_PORT) so the India scraper can POST counts."
        )

    def on_message(client: "NewClient", message) -> None:
        if not message.Info or not message.Info.MessageSource:
            return
        source = message.Info.MessageSource
        chat = source.Chat
        if getattr(chat, "Server", "") != "g.us":
            return
        body = _get_text(message)
        if not body:
            return
        lower = body.strip().lower()
        if lower != "!sih" and not lower.startswith("!sih "):
            return

        actor = gate(session_factory, source.Sender, client, chat, "member", "sih")
        if not actor:
            return

        args = body.strip()[4:].strip()
        try:
            if not args:
                rows = store.list_problem_statements()
                client.send_message(chat, _format_summary(rows, threshold))
                return
            if args.lower() == "hot":
                rows = store.list_problem_statements(min_count=threshold)
                client.send_message(chat, _format_hot(rows, threshold))
                return
            if args.lower() == "refresh":
                if not scraper_url or not scraper_secret:
                    client.send_message(
                        chat,
                        "SIH refresh is not configured. Set SIH_SCRAPER_URL and SIH_SCRAPER_SECRET.",
                    )
                    return
                result = trigger_remote_scrape(scraper_url, scraper_secret)
                scraped = result.get("scraped")
                ingest = result.get("ingest") if isinstance(result.get("ingest"), dict) else {}
                alerts = ingest.get("alerts_sent")
                extra = ""
                if scraped is not None:
                    extra = f" Scraped {scraped} PS"
                    if alerts is not None:
                        extra += f", {alerts} new alert(s)"
                    extra += "."
                client.send_message(chat, "SIH refresh complete." + extra)
                return
            lower_args = args.lower()
            if lower_args.startswith("add dsce"):
                tokens = args[8:].strip()
                ps_numbers = parse_ps_list(tokens)
                if not ps_numbers:
                    client.send_message(
                        chat,
                        "Usage: `!sih add dsce 26001, 26002` (or `SIH26001`).",
                    )
                    return
                if len(ps_numbers) > WATCHLIST_ADD_LIMIT:
                    client.send_message(chat, f"Add at most {WATCHLIST_ADD_LIMIT} PS at a time.")
                    return
                added, already = store.add_watchlist(DSCE_LIST, ps_numbers)
                bits = []
                if added:
                    bits.append("added " + ", ".join(f"`{n}`" for n in added))
                if already:
                    bits.append("already tracked " + ", ".join(f"`{n}`" for n in already))
                client.send_message(chat, "DSCE watchlist: " + "; ".join(bits) + ".")
                return
            if lower_args.startswith("remove dsce"):
                tokens = args[11:].strip()
                ps_numbers = parse_ps_list(tokens)
                if not ps_numbers:
                    client.send_message(
                        chat,
                        "Usage: `!sih remove dsce 26001, 26002`.",
                    )
                    return
                removed, missing = store.remove_watchlist(DSCE_LIST, ps_numbers)
                bits = []
                if removed:
                    bits.append("removed " + ", ".join(f"`{n}`" for n in removed))
                if missing:
                    bits.append("not on list " + ", ".join(f"`{n}`" for n in missing))
                client.send_message(chat, "DSCE watchlist: " + "; ".join(bits) + ".")
                return
            if lower_args == "dsce" or lower_args.startswith("dsce "):
                rest = args[4:].strip()
                rest_lower = rest.lower()
                limit = 10
                if rest_lower == "top" or rest_lower.startswith("top "):
                    _, _, ntext = rest.partition(" ")
                    try:
                        limit = int(ntext.strip() or "10")
                    except ValueError:
                        limit = 10
                elif rest:
                    try:
                        limit = int(rest)
                    except ValueError:
                        limit = 10
                limit = max(1, min(limit, 25))
                ranked = store.list_watchlist_ranked(DSCE_LIST)
                if not ranked:
                    client.send_message(
                        chat,
                        "DSCE watchlist is empty. Add PS with `!sih add dsce 26001, 26002`.",
                    )
                    return
                shown = ranked[:limit]
                lines = [f"*DSCE watchlist top {len(shown)}* ({len(ranked)} tracked)", ""]
                lines.extend(_format_ps_line(row) for row in shown)
                client.send_message(chat, "\n".join(lines))
                return
            if args.lower().startswith("top"):
                _, _, rest = args.partition(" ")
                try:
                    limit = int(rest.strip() or "10")
                except ValueError:
                    limit = 10
                limit = max(1, min(limit, 25))
                rows = store.list_problem_statements()[:limit]
                lines = [f"*SIH 2026 top {len(rows)}*", ""]
                lines.extend(_format_ps_line(row) for row in rows)
                client.send_message(chat, "\n".join(lines) if rows else "No PS stored yet.")
                return

            ps_number = args.split()[0].strip()
            row = store.get(ps_number)
            if row is None:
                client.send_message(chat, f"No stored SIH PS matching `{public_text(ps_number, limit=40)}`.")
                return
            client.send_message(chat, _format_one(row, threshold))
        except Exception:
            log.exception("SIH command failed")
            client.send_message(chat, "Could not complete that SIH request.")

    log.info("SIH 2026 feature registered")
    return on_message
