#!/usr/bin/env python3
"""Script to list all joined WhatsApp groups and their IDs."""

import logging
import sys
import threading
import time
from pathlib import Path

from neonize.client import NewClient
from neonize.events import ConnectedEv

# Silence neonize's debug logs to make the output clean
logging.basicConfig(level=logging.WARNING)

SESSION_DB = Path.cwd() / "neonize.db"

if not SESSION_DB.exists():
    print("⚠️ neonize.db not found. Please run the main bot first to link your account.")
    sys.exit(1)

client = NewClient(str(SESSION_DB))

def fetch_and_print_groups(client: NewClient):
    print("✅ Connected to WhatsApp. Waiting a few seconds for data sync...")
    time.sleep(5)  # Give it a moment to sync groups from the server
    
    try:
        groups = client.get_joined_groups()
        print(f"\nFound {len(groups)} joined groups:\n")
        print("=" * 60)
        
        for group in groups:
            # Depending on neonize version, the JID and Name fields might differ slightly
            # because they are mapped from protobufs/Go structs.
            jid = ""
            if hasattr(group, "JID"):
                jid_obj = group.JID
                user = getattr(jid_obj, "User", "")
                server = getattr(jid_obj, "Server", "")
                jid = f"{user}@{server}"
            else:
                jid = str(getattr(group, "jid", "Unknown"))
                
            name = "Unknown"
            if hasattr(group, "GroupName"):
                name_obj = group.GroupName
                name = getattr(name_obj, "Name", str(name_obj))
            elif hasattr(group, "Name"):
                name = group.Name
            elif hasattr(group, "name"):
                name = group.name

            print(f"Name : {name}")
            print(f"ID   : {jid}")
            print("-" * 60)
            
    except Exception as e:
        print(f"❌ Error fetching groups: {e}")
    finally:
        print("\nExiting...")
        # Disconnect gracefully
        client.disconnect()
        # Force exit because the neonize event loop might hang
        import os
        os._exit(0)

@client.event(ConnectedEv)
def on_connected(client: NewClient, _event: ConnectedEv):
    # Run the fetch in a background thread so we don't block the neonize event loop
    threading.Thread(target=fetch_and_print_groups, args=(client,), daemon=True).start()

if __name__ == "__main__":
    print("Connecting to WhatsApp...")
    client.connect()
