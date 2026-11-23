"""Select the local IPv4 address advertised for WebRTC media."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

log = logging.getLogger("vauxr.realtime")


def effective_host(configured: str) -> str:
    """Use an explicit ICE host or the local address of the trusted owner origin.

    Never use a client-supplied Host header: it is not a trusted media address.
    An origin behind a proxy may resolve elsewhere; those installs need an
    explicit REALTIME_HOST (or realtime.host in config.json).
    """
    if configured:
        return configured

    from vauxr.auth.owner import configured_origin

    origin_host = urlsplit(configured_origin()).hostname
    if not origin_host:
        return ""
    try:
        resolved = {
            info[4][0]
            for info in socket.getaddrinfo(origin_host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        }
        if "127.0.0.1" in resolved and all(ipaddress.ip_address(address).is_loopback for address in resolved):
            return "127.0.0.1"
        from aioice.ice import get_host_addresses

        local = get_host_addresses(use_ipv4=True, use_ipv6=False)
    except (ImportError, OSError) as exc:
        log.warning("Could not detect a local WebRTC address for %s: %s", origin_host, exc)
        return ""
    selected = next(
        (
            address
            for address in local
            if address in resolved and not ipaddress.ip_address(address).is_loopback
        ),
        "",
    )
    if not selected:
        log.warning(
            "Owner origin %s does not resolve to a local IPv4 address; set REALTIME_HOST for WebRTC",
            origin_host,
        )
    return selected
