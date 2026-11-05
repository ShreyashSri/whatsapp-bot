"""Read-only WhatsApp summary of recruitment registrations in MongoDB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
from typing import TYPE_CHECKING, Any

from pymongo import MongoClient

from db.auth import gate
from features.subgroups import _get_text

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

COMMANDS = {"!registration-stats", "!reg-stats"}
FIELD_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
YEAR_LABELS = {1: "1st Year", 2: "2nd Year", 3: "3rd Year"}


def _valid_field_path(value: str, setting_name: str) -> str:
    path = (value or "").strip()
    if not FIELD_PATH_RE.fullmatch(path):
        raise ValueError(f"{setting_name} must be a MongoDB field path")
    return path


def _year_number(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value in YEAR_LABELS else None

    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
    aliases = {
        1: {"1", "1st", "1styear", "first", "firstyear", "year1", "year1st"},
        2: {"2", "2nd", "2ndyear", "second", "secondyear", "year2", "year2nd"},
        3: {"3", "3rd", "3rdyear", "third", "thirdyear", "year3", "year3rd"},
    }
    for year, accepted in aliases.items():
        if normalized in accepted:
            return year
    return None


def _load_counts(collection, timestamp_field: str, year_field: str) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    pipeline = [
        {
            "$addFields": {
                "_registration_stats_timestamp": {
                    "$convert": {
                        "input": f"${timestamp_field}",
                        "to": "date",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }
        },
        {
            "$facet": {
                "total": [{"$count": "count"}],
                "last_24_hours": [
                    {"$match": {"_registration_stats_timestamp": {"$gte": cutoff}}},
                    {"$count": "count"},
                ],
                "years": [
                    {"$group": {"_id": f"${year_field}", "count": {"$sum": 1}}}
                ],
            }
        },
    ]
    result = next(iter(collection.aggregate(pipeline, maxTimeMS=10000)), {})

    def count_for(key: str) -> int:
        rows = result.get(key) or []
        return int(rows[0].get("count", 0)) if rows else 0

    years = {year: 0 for year in YEAR_LABELS}
    unclassified = 0
    for row in result.get("years") or []:
        year = _year_number(row.get("_id"))
        count = int(row.get("count", 0))
        if year in years:
            years[year] += count
        else:
            unclassified += count

    return {
        "total": count_for("total"),
        "last_24_hours": count_for("last_24_hours"),
        "years": years,
        "unclassified": unclassified,
    }


def _format_stats(stats: dict[str, Any]) -> str:
    lines = [
        "📊 *Registration Stats*",
        "",
        f"• *Total Registrations:* {stats['total']:,}",
        f"• *Last 24 Hours:* {stats['last_24_hours']:,}",
        "",
        "🎓 *Year-wise Breakdown:*",
    ]
    for year, label in YEAR_LABELS.items():
        lines.append(f"  • {label}: *{stats['years'][year]:,}*")
    if stats["unclassified"]:
        lines.append(f"  • Other / Unspecified: *{stats['unclassified']:,}*")
    return "\n".join(lines)


def register(client: "NewClient", config: dict):
    uri = (config.get("mongo_uri") or "").strip()
    database_name = (config.get("mongo_database") or "").strip()
    collection_name = (config.get("mongo_collection") or "").strip()
    timestamp_field = (config.get("mongo_timestamp_field") or "createdAt").strip()
    year_field = (config.get("mongo_year_field") or "year_of_study").strip()
    session_factory = config.get("db_session_factory")
    if session_factory is None:
        log.error("Registration stats require the bot database session for member authorization")
        return None

    mongo_collection = None
    if uri and database_name and collection_name:
        try:
            timestamp_field = _valid_field_path(timestamp_field, "MONGO_TIMESTAMP_FIELD")
            year_field = _valid_field_path(year_field, "MONGO_YEAR_FIELD")
            mongo_client = MongoClient(
                uri,
                connectTimeoutMS=5000,
                serverSelectionTimeoutMS=5000,
                socketTimeoutMS=10000,
            )
            mongo_collection = mongo_client[database_name][collection_name]
        except Exception:
            log.exception("Recruitment registration stats configuration is invalid")
    else:
        log.warning(
            "Registration stats are disabled until MONGO_URI, MONGO_DB, and "
            "MONGO_COLLECTION are configured."
        )

    def on_message(client: "NewClient", message) -> None:
        if not message.Info or not message.Info.MessageSource:
            return
        source = message.Info.MessageSource
        chat = source.Chat
        if getattr(chat, "Server", "") != "g.us":
            return

        body = _get_text(message).strip().lower()
        if body not in COMMANDS:
            return

        actor = gate(session_factory, source.Sender, client, chat, "member", "registration_stats")
        if not actor:
            return

        if mongo_collection is None:
            client.send_message(chat, "⚠️ Registration stats are not configured yet.")
            return

        try:
            stats = _load_counts(mongo_collection, timestamp_field, year_field)
            client.send_message(chat, _format_stats(stats))
        except Exception:
            log.exception("Failed to load recruitment registration statistics")
            client.send_message(chat, "⚠️ I couldn't load registration stats right now.")

    return on_message
