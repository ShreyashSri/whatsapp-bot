"""Administrative user and role commands."""
from __future__ import annotations
import logging
from db.auth import (gate, normalize_jid, remove_or_demote_user, upsert_user)
from features.subgroups import _get_mentioned_jids, _get_text
from db.models import User
from features.text import public_text

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
        command, _, args = body.partition(" ")
        cmd = command.lower()

        # !admins requires only member permission
        if cmd in ("!admins", "!admin-list", "!admins-list") or (cmd == "!admins" and args.strip() == "list"):
            if not gate(factory, source.Sender, client, chat, "member", "admin.list"): return
            with factory() as session:
                admins = session.query(User).filter(User.role == "admin", User.active.is_(True)).order_by(User.jid).all()
            if not admins: reply(chat, "📭 No active admins found.")
            else: reply(chat, "*👥 Active Admins*\n" + "\n".join(f"• {public_text(u.display_name or ('member' if u.jid.endswith('@lid') else u.jid), limit=80)}" for u in admins))
            return

        # Commands requiring active admin access (!add-user, !remove-user, !users)
        actor = gate(factory, source.Sender, client, chat, "admin", cmd)
        if not actor: return
        actor_jid = normalize_jid(source.Sender)  # for logging only
        try:
            runtime_mentions = getattr(message, "_pbbot_runtime_mentions", None)
            mentions = (
                list(runtime_mentions)
                if runtime_mentions is not None
                else _get_mentioned_jids(message)
            )
            if cmd == "!add-user":
                tokens = [t.strip().lower() for t in args.replace("|", " ").split()]
                role = next((t for t in tokens if t in ("admin", "member")), "") or "member"
                if not mentions:
                    reply(chat, "⚠️ Mention a user and role (e.g. `@bot make @user1 an admin`)."); return
                for jid in mentions:
                    upsert_user(factory, jid, role, actor=actor)
                reply(chat, f"✅ add-user ({role}) completed for {len(mentions)} user(s).")
            elif cmd == "!remove-user":
                if not mentions:
                    reply(chat, "⚠️ Mention a user to remove (e.g. `@bot remove @user1 from the bot`)."); return
                results = []
                for jid in mentions:
                    _, action = remove_or_demote_user(factory, jid, actor=actor)
                    results.append(
                        f"{public_text('member' if normalize_jid(jid).endswith('@lid') else normalize_jid(jid).split('@')[0], limit=80)} ({action})"
                    )
                reply(chat, f"✅ Processed {len(mentions)} user(s): {', '.join(results)}.")
            else:
                with factory() as session:
                    users = session.query(User).order_by(User.jid).all()
                if not users: reply(chat, "📭 No users configured.")
                else: reply(chat, "*👥 All Users*\n" + "\n".join(f"• {public_text(u.display_name or ('member' if u.jid.endswith('@lid') else u.jid), limit=80)} — {u.role}{'' if u.active else ' (inactive)'}" for u in users))
        except ValueError as exc:
            log.info("Rejected admin operation: %s", exc)
            reply(chat, "⚠️ That user administration request could not be completed.")
    return on_message
