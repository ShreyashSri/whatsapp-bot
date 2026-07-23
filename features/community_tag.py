"""Community Group Tagging Feature."""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from neonize.events import MessageEv

if TYPE_CHECKING:
    from neonize.client import NewClient

log = logging.getLogger(__name__)

def _get_text(message: MessageEv) -> str:
    """Extract text body from a message."""
    text = message.Message.conversation or ""
    if message.Message.extendedTextMessage and message.Message.extendedTextMessage.text:
        text = message.Message.extendedTextMessage.text
    elif message.Message.imageMessage and message.Message.imageMessage.caption:
        text = message.Message.imageMessage.caption
    return text.strip()

def register(client: "NewClient", config: dict) -> callable:
    def on_message(client: "NewClient", message: MessageEv):
        if not message.Info or not message.Info.MessageSource:
            return
            
        chat = message.Info.MessageSource.Chat
        if getattr(chat, "Server", "") != "g.us":
            return
            
        # Ignore our own messages to prevent infinite loops
        if message.Info.MessageSource.IsFromMe:
            return
            
        body = _get_text(message)
        if not body:
            return
            
        lower_body = body.lower()
        
        try:
            groups = client.get_joined_groups()
            chat_info = client.get_group_info(chat)
        except Exception as exc:
            log.error("Failed to get joined groups or chat info: %s", exc)
            return
            
        chat_parent = getattr(chat_info, "GroupLinkedParent", None)
        chat_parent_user = getattr(chat_parent, "User", "") if chat_parent else ""
        
        # If the chat itself is the community announcement group, we might need its JID
        if not chat_parent_user and getattr(chat_info, "GroupIsDefaultSub", False):
             chat_parent_user = chat.User
             
        # Filter groups to only those in the same community (or the chat itself)
        community_groups = []
        for g in groups:
            g_parent = getattr(g, "GroupLinkedParent", None)
            g_parent_user = getattr(g_parent, "User", "") if g_parent else ""
            if (chat_parent_user and g_parent_user == chat_parent_user) or (g.JID.User == chat.User):
                community_groups.append(g)
            
        # Sort by longest name first to avoid matching "Dev" inside "@Dev Team"
        sorted_groups = sorted(
            community_groups, 
            key=lambda g: len(getattr(g.GroupName, "Name", getattr(g, "Name", getattr(g, "name", "")))), 
            reverse=True
        )
        
        matched_groups = []
        for g in sorted_groups:
            name = getattr(g.GroupName, "Name", getattr(g, "Name", getattr(g, "name", "")))
            if not name:
                continue
                
            mention_str = f"@{name.lower()}"
            if mention_str in lower_body:
                matched_groups.append((g, name))
                # Remove matched mention to avoid double matching shorter sub-names
                lower_body = lower_body.replace(mention_str, "")
                
        if not matched_groups:
            return
            
        for group, name in matched_groups:
            try:
                ginfo = client.get_group_info(group.JID)
            except Exception as e:
                log.error("Failed to get group info for %s: %s", name, e)
                continue
                
            mentions = []
            if hasattr(ginfo, "Participants"):
                for p in ginfo.Participants:
                    if hasattr(p, "JID"):
                        user = getattr(p.JID, "User", "")
                        if user:
                            mentions.append(f"@{user}")
                            
            if mentions:
                ghost_str = " ".join(mentions)
                text = f"📢 Tagging all members of {name}"
                try:
                    client.send_message(chat, text, ghost_mentions=ghost_str)
                    log.info("Sent community tag for group: %s", name)
                except Exception as e:
                    log.error("Failed to send community tag message: %s", e)

    log.info("✅ Community tag feature registered")
    return on_message
