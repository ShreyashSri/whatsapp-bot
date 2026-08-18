"""Seed the first administrator: python -m db.seed_admin JID [--force-role]."""
from __future__ import annotations
import argparse, os
from dotenv import load_dotenv
load_dotenv()
from .auth import normalize_jid, upsert_user
from .database import create_database
from .models import User

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("jid"); parser.add_argument("--force-role", action="store_true")
    db_url = os.getenv("DATABASE_URL") or "postgresql://pbbot:pbbot@localhost:5432/pbbot"
    args = parser.parse_args(); db = create_database(db_url); db.initialize()
    existing = db.session_factory()
    try: user = existing.get(User, normalize_jid(args.jid))
    finally: existing.close()
    if user and not args.force_role:
        print(f"Existing user preserved: {user.jid} ({user.role})"); return
    upsert_user(db.session_factory, args.jid, "admin")
    print(f"Seeded admin: {normalize_jid(args.jid)}")
if __name__ == "__main__": main()
