"""Reminders Feature (PRD FR-7).

Admin commands:
  !reminder-config [frequency N] [| window HH:MM-HH:MM] [| threshold N] [| channel JID]
  !reminder-run           — trigger scheduled reminder run idempotently
  !reminder-history [id]  — view reminder execution history logs
  !reminders              — show reminder status & config summary
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.auth import gate, normalize_group_jid, normalize_jid
from db.reminder_store import ReminderStore
from features.subgroups import _get_mentioned_jids, _get_text
from features.text import public_error, public_text, split_command_fields

if TYPE_CHECKING:
    from neonize.client import NewClient
    from neonize.events import MessageEv

log = logging.getLogger(__name__)

REMINDER_CMDS = (
    "!reminders",
    "!reminder-config",
    "!reminder-run",
    "!reminder-history",
)


def _fmt_config(cfg: dict) -> str:
    channel = cfg.get("escalation_channel")
    chan = "configured admin channel" if channel else "none"
    return (
        f"⚙️ *Reminder Configuration*\n"
        f"• Frequency: `{cfg['frequency_hours']}h`\n"
        f"• Active Window: `{cfg['active_window_start']} – {cfg['active_window_end']} UTC`\n"
        f"• Escalation Threshold: `{cfg['escalation_threshold']} missed reminders`\n"
        f"• Escalation Channel: `{chan}`"
    )


def configured_reminder_group(config: dict) -> str | None:
    """Return the configured team chat used for multi-assignee reminders."""
    candidates = [config.get("reminder_group_id"), config.get("pbbot_group_id")]
    candidates.extend(config.get("group_ids", set()) or set())
    for candidate in candidates:
        normalized = normalize_group_jid(candidate)
        if normalized:
            return normalized
    return None


def _parse_config_args(args: str, mentions: list[str]) -> dict:
    result: dict = {}
    parts = [p.strip() for p in split_command_fields(args) if p.strip()]
    for part in parts:
        colon_key = part.split(":", 1)[0].strip().lower() if ":" in part else ""
        if colon_key in ("frequency", "frequency_hours", "freq", "f", "window", "active_window", "w", "threshold", "escalation_threshold", "t", "channel", "escalation_channel", "c"):
            k, _, v = part.partition(":")
            k, v = k.strip().lower(), v.strip()
        else:
            k, _, v = part.partition(" ")
            k, v = k.strip().lower(), v.strip()
        if k in ("frequency", "frequency_hours", "freq", "f"):
            if v.isdigit():
                result["frequency_hours"] = int(v)
        elif k in ("window", "active_window", "w"):
            if "-" in v:
                w_start, _, w_end = v.partition("-")
                result["active_window_start"] = w_start.strip()
                result["active_window_end"] = w_end.strip()
        elif k in ("threshold", "escalation_threshold", "t"):
            if v.isdigit():
                result["escalation_threshold"] = int(v)
        elif k in ("channel", "escalation_channel", "c"):
            if mentions:
                result["escalation_channel"] = mentions[0]
            elif v:
                result["_channel_error"] = True
    if mentions and "escalation_channel" not in result:
        result["escalation_channel"] = mentions[0]
    return result


def _cmd_config(client: "NewClient", chat, args: str, actor, mentions: list[str], store: ReminderStore) -> None:
    if not args.strip():
        cfg = store.get_config()
        client.send_message(chat, _fmt_config(cfg))
        return

    parsed = _parse_config_args(args, mentions)
    if parsed.pop("_channel_error", False):
        client.send_message(chat, "⚠️ Select the escalation channel with a native WhatsApp @mention.")
        return
    if not parsed:
        client.send_message(
            chat,
            "⚠️ Usage: `!reminder-config [frequency 24] [| window 09:00-18:00] [| threshold 3] [| channel @admin]`",
        )
        return

    try:
        updated = store.update_config(
            actor=actor,
            frequency_hours=parsed.get("frequency_hours"),
            active_window_start=parsed.get("active_window_start"),
            active_window_end=parsed.get("active_window_end"),
            escalation_threshold=parsed.get("escalation_threshold"),
            escalation_channel=parsed.get("escalation_channel"),
        )
        client.send_message(chat, f"✅ Reminder config updated!\n\n{_fmt_config(updated)}")
    except ValueError as exc:
        client.send_message(chat, f"⚠️ {public_error(exc, 'I could not update the reminder configuration.')}")


def _cmd_run(
    client: "NewClient", chat, actor, store: ReminderStore,
    *, group_jid: str | None = None,
) -> None:
    res = store.run_reminders(
        client, actor, force_ignore_window=True, group_jid=group_jid,
    )
    msg = (
        f"⚡ *Reminder Run Completed*\n"
        f"• Eligible assignments: `{res['eligible']}`\n"
        f"• Reminders sent: `{res['sent']}`\n"
        f"• Escalations triggered: `{res['escalated']}`\n"
        f"• Delivery failures: `{res['failed']}`"
    )
    client.send_message(chat, msg)


def _cmd_remind(
    client: "NewClient", chat, args: str, actor, store: ReminderStore,
    *, group_jid: str | None = None,
) -> None:
    """Send an immediate reminder for one event/task within the actor's scope."""
    tokens = args.split()
    if len(tokens) != 2 or tokens[0].lower() not in {"event", "task"} or not tokens[1].isdigit():
        client.send_message(
            chat,
            "⚠️ Usage: `!work reminders remind <event|task> <id>`",
        )
        return
    target_type, target_id = tokens[0].lower(), int(tokens[1])
    result = store.run_reminders(
        client,
        actor,
        force_ignore_window=True,
        source="whatsapp",
        group_jid=group_jid,
        target_type=target_type,
        target_id=target_id,
        user_jid=None if actor.role == "admin" else actor.jid,
        ignore_idempotency=True,
    )
    client.send_message(
        chat,
        f"🔔 Reminder sent for `{target_type} {target_id}` to "
        f"`{result['sent'] + result['escalated']}` assignment(s)."
        if result["eligible"]
        else f"📭 No open assignment for `{target_type} {target_id}` is in your scope.",
    )


def _cmd_history(client: "NewClient", chat, args: str, store: ReminderStore, *, actor=None) -> None:
    raw_assignment_id = args.strip()
    if raw_assignment_id and not raw_assignment_id.isdigit():
        client.send_message(chat, "⚠️ Usage: `!reminder-history [assignment_id]`")
        return
    assignment_id = int(raw_assignment_id) if raw_assignment_id else None
    # Members can inspect only their own reminder history. Admins can inspect
    # all history, or narrow it to one assignment.
    user_jid = None if actor is None or actor.role == "admin" else actor.jid
    logs = store.get_history(assignment_id=assignment_id, user_jid=user_jid, limit=20)
    if not logs:
        client.send_message(chat, "📭 No reminder history records found.")
        return

    lines = ["📜 *Reminder History*", ""]
    for log_entry in logs:
        ts = log_entry["timestamp"].strftime("%Y-%m-%d %H:%M UTC")
        assignment_info = f"Assignment #{log_entry['assignment_id']}"
        res = log_entry["result"].upper()
        details = f" ({public_text(log_entry['details'], limit=180)})" if log_entry["details"] else ""
        lines.append(f"[{ts}] *{assignment_info}* [{res}]{details}")

    client.send_message(chat, "\n".join(lines))


def _cmd_reminders_summary(client: "NewClient", chat, store: ReminderStore, *, actor=None) -> None:
    cfg = store.get_config()
    user_jid = None if actor is None or actor.role == "admin" else actor.jid
    eligible = store.get_eligible_assignments(force_ignore_window=True, user_jid=user_jid)
    within_window = store.is_within_active_window()

    msg = (
        f"⏰ *Reminder System Status*\n"
        f"• Window Active: `{'Yes' if within_window else 'No'}`\n"
        f"• Eligible Pending Reminders: `{len(eligible)}`\n\n"
        f"{_fmt_config(cfg)}"
    )
    client.send_message(chat, msg)


def register(client: "NewClient", config: dict) -> callable:
    factory = config.get("db_session_factory")
    if factory is None:
        raise RuntimeError("Reminders feature requires db_session_factory")
    store = ReminderStore(factory)
    reminder_group_jid = configured_reminder_group(config)

    def on_message(client: "NewClient", message: "MessageEv"):
        if not message.Info or not message.Info.MessageSource:
            return
        source = message.Info.MessageSource
        chat = source.Chat
        if getattr(chat, "Server", "") != "g.us":
            return

        body = _get_text(message)
        if not body:
            return

        lower = body.lower()
        if not any(lower == cmd or lower.startswith(f"{cmd} ") for cmd in REMINDER_CMDS):
            return

        if reminder_group_jid and normalize_jid(chat) != reminder_group_jid:
            client.send_message(chat, "⛔ Reminders are managed in the designated reminder group.")
            return

        command, _, args = body.partition(" ")
        cmd = command.lower()
        sub = args.strip().lower()

        mentions = _get_mentioned_jids(message)

        if cmd == "!reminder-config" or (cmd == "!reminders" and sub.startswith("config")):
            actor = gate(factory, source.Sender, client, chat, "admin", "reminder.config")
            if not actor:
                return
            sub_args = args[len("config"):].strip() if cmd == "!reminders" else args
            _cmd_config(client, chat, sub_args, actor, mentions, store)

        elif cmd == "!reminder-run" or (cmd == "!reminders" and sub == "run"):
            actor = gate(factory, source.Sender, client, chat, "admin", "reminder.run")
            if not actor:
                return
            _cmd_run(client, chat, actor, store, group_jid=configured_reminder_group(config))

        elif cmd == "!reminder-history" or (cmd == "!reminders" and sub.startswith("history")):
            actor = gate(factory, source.Sender, client, chat, "member", "reminder.history")
            if not actor:
                return
            sub_args = args[len("history"):].strip() if cmd == "!reminders" else args
            _cmd_history(client, chat, sub_args, store, actor=actor)

        elif cmd == "!reminders":
            actor = gate(factory, source.Sender, client, chat, "member", "reminder.status")
            if not actor:
                return
            _cmd_reminders_summary(client, chat, store, actor=actor)

    log.info("✅ Reminders feature registered")
    return on_message
