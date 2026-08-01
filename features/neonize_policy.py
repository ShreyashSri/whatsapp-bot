"""Versioned policy for the installed Neonize client surface.

Neonize exposes a much larger client than the bot should hand to an LLM.
Every public callable is classified here as either an exposed, typed tool
adapter or an intentional exclusion.  The audit is deliberately executable
so a Neonize upgrade cannot silently add an unreviewed capability.
"""

from __future__ import annotations

EXPOSED_NEONIZE_METHODS = frozenset({
    "build_document_message", "build_image_message", "build_poll_vote_creation",
    "build_reaction", "create_group", "download_any", "get_blocklist",
    "get_contact_qr_link", "get_group_info", "get_group_info_from_link", "get_group_invite_link", "get_me",
    "get_group_request_participants", "get_joined_groups", "get_lid_from_pn",
    "get_linked_group_participants", "get_pn_from_lid", "get_profile_picture",
    "get_sub_groups", "get_user_devices", "get_user_info", "is_on_whatsapp",
    "join_group_with_link", "leave_group", "pin_message", "reply_message",
    "revoke_message", "send_audio", "send_contact", "send_document",
    "send_image", "send_message", "send_sticker", "send_video",
    "set_disappearing_timer", "set_group_announce", "set_group_locked",
    "set_group_name", "set_group_photo", "set_group_topic",
    "update_blocklist", "update_group_participants", "link_group", "unlink_group",
    "set_profile_name", "set_status_message", "set_profile_photo",
})


_EXCLUDED_GROUPS = {
    "session and transport operations": (
        "PairPhone", "connect", "connect_with_proxy", "disconnect", "logout",
        "stop", "set_passive", "set_proxy_address", "set_force_activate_delivery_receipts",
        "send_app_state", "generate_message_id",
    ),
    "unsupported or untrusted media/protocol primitives": (
        "build_album_content", "build_audio_message", "build_poll_vote", "build_reply_message",
        "build_revoke", "build_sticker_message", "build_stickerpack_message", "build_video_message",
        "download_media_with_path", "decrypt_poll_vote", "edit_message", "get_message_for_retry",
        "send_album", "send_fb_message", "send_interactive_message", "send_presence", "send_stickerpack",
        "send_chat_presence", "upload",
    ),
    "newsletter surface outside this bot's group/task domain": (
        "create_newsletter", "follow_newsletter", "get_newsletter_info",
        "get_newsletter_info_with_invite", "get_newsletter_message_update",
        "get_newsletter_messages", "get_subscribed_newletters", "newsletter_mark_viewed",
        "newsletter_send_reaction", "newsletter_subscribe_live_updates",
        "newsletter_toggle_mute", "unfollow_newsletter", "upload_newsletter",
    ),
    "global account/privacy surface requires a separate explicit admin subsystem": (
        "get_privacy_settings", "get_status_privacy",
        "resolve_business_message_link", "resolve_contact_qr_link", "set_default_disappearing_timer",
        "set_privacy_setting",
        "subscribe_presence", "mark_read",
    ),
    "cross-group or protocol-link operations require explicit entity lifecycle design": (
        "get_group_info_from_invite", "join_group_with_invite",
    ),
}

EXCLUDED_NEONIZE_METHODS = {
    method: reason
    for reason, methods in _EXCLUDED_GROUPS.items()
    for method in methods
}


def audit_neonize_surface(client_type=None) -> dict[str, list[str]]:
    """Return unknown and stale policy entries for a Neonize client class."""
    if client_type is None:
        from neonize.client import NewClient

        client_type = NewClient
    public = set()
    for name in dir(client_type):
        if name.startswith("_"):
            continue
        try:
            if callable(getattr(client_type, name)):
                public.add(name)
        except Exception:
            continue
    classified = set(EXPOSED_NEONIZE_METHODS) | set(EXCLUDED_NEONIZE_METHODS)
    return {
        "unclassified": sorted(public - classified),
        "stale_exposed": sorted(EXPOSED_NEONIZE_METHODS - public),
        "stale_excluded": sorted(set(EXCLUDED_NEONIZE_METHODS) - public),
        "conflicts": sorted(set(EXPOSED_NEONIZE_METHODS) & set(EXCLUDED_NEONIZE_METHODS)),
    }


def policy_reason(method: str) -> str:
    """Explain why a method is or is not available to the planner."""
    if method in EXPOSED_NEONIZE_METHODS:
        return "exposed through a typed, authorized agent tool"
    return EXCLUDED_NEONIZE_METHODS.get(method, "not classified")
