"""Administrative user and role commands."""
from __future__ import annotations
import logging
from db.auth import (normalize_jid, remove_or_demote_user, require_admin, require_member,
                     upsert_user)
from features.subgroups import _get_mentioned_jids, _get_text
from db.models import User

log = logging.getLogger(__name__)

def register(client, config):
    factory = config["db_session_factory"]
    def reply(chat, text): client.send_message(chat, text)
    def on_message(client, message):
        if not message.Info or not message.Info.MessageSource: return
        source = message.Info.MessageSource; chat = source.Chat
        # Allow group chat processing for both group members and owner account
        if getattr(chat, "Server", "") != "g.us": return
        body = _get_text(message); lower = body.lower()
        if not lower.startswith(("!add-user", "!remove-user", "!users", "!admins", "!admin-list", "!admins-list")): return
        actor_jid = normalize_jid(source.Sender)
        command, _, args = body.partition(" ")
        cmd = command.lower()
        
        # !admins / !admins list requires only member permission
        if cmd in ("!admins", "!admin-list", "!admins-list") or (cmd == "!admins" and args.strip() == "list"):
            actor = require_member(factory, actor_jid, "admin.list")
            if not actor:
                reply(chat, "⛔ An active user account is required."); return
            with factory() as session:
                admins = session.query(User).filter(User.role == "admin", User.active.is_(True)).order_by(User.jid).all()
            if not admins: reply(chat, "📭 No active admins found.")
            else: reply(chat, "*👥 Active Admins*\n" + "\n".join(f"• {u.display_name or u.jid}" for u in admins))
            return

        # Commands requiring active admin access (!add-user, !remove-user, !users)
        actor = require_admin(factory, actor_jid, cmd)
        if not actor:
            log.warning("Admin access denied for sender=%s (actor_jid=%s)", source.Sender, actor_jid)
            reply(chat, "⛔ You need to be an active administrator to use this command."); return
        try:
            mentions = _get_mentioned_jids(message)
            if cmd == "!add-user":
                tokens = [t.strip().lower() for t in args.replace("|", " ").split()]
                role = next((t for t in tokens if t in ("admin", "member")), "") or "member"
                if not mentions:
                    reply(chat, "⚠️ Usage: `!add-user [admin|member] @person`"); return
                for jid in mentions:
                    upsert_user(factory, jid, role, actor=actor)
                reply(chat, f"✅ add-user ({role}) completed for {len(mentions)} user(s).")
            elif cmd == "!remove-user":
                if not mentions:
                    reply(chat, "⚠️ Usage: `!remove-user @person`"); return
                results = []
                for jid in mentions:
                    _, action = remove_or_demote_user(factory, jid, actor=actor)
                    results.append(f"{jid.split('@')[0]} ({action})")
                reply(chat, f"✅ Processed {len(mentions)} user(s): {', '.join(results)}.")
            else:
                with factory() as session:
                    users = session.query(User).order_by(User.jid).all()
                if not users: reply(chat, "📭 No users configured.")
                else: reply(chat, "*👥 All Users*\n" + "\n".join(f"• {u.display_name or u.jid} — {u.role}{'' if u.active else ' (inactive)'}" for u in users))
        except ValueError as exc:
            log.info("Rejected admin operation actor=%s: %s", actor_jid, exc)
            reply(chat, f"⚠️ {exc}")
    return on_message
