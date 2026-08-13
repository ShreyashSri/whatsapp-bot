"""Network URL policy shared by NL metadata and card rendering."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def _public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def safe_public_url(value: str) -> bool:
    """Reject local, reserved, credential-bearing, or unresolvable URLs."""
    try:
        parsed = urlparse(str(value or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            return False
        try:
            if not _public_ip(hostname):
                return False
            return True
        except ValueError:
            pass
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(
                    hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            }
        except (OSError, ValueError):
            return False
        return bool(addresses) and all(_public_ip(address) for address in addresses)
    except ValueError:
        return False
