"""Realtime (WebRTC/Pipecat) HTTP wiring for the vauxr aiohttp app.

Only imported when REALTIME_ENABLED=1. Adds the authenticated SmallWebRTC
`/api/offer` endpoint and applies the aiortc DTLS cipher patch required for the
firmware's esp_peer (RSA cert) to complete the DTLS handshake.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

import auth_connections
import channel_registry
from auth import authenticate, current, get_store
from auth_policy import Operation, Principal, Role, allowed, audit_denial
from config import get_config
from http_server import transport_boundary

log = logging.getLogger("vauxr.realtime")
_media_authorities: dict[str, list[auth_connections.Connection]] = {}


def broaden_aiortc_dtls_ciphers() -> None:
    """Let aiortc accept ECDHE-RSA DTLS suites, not just ECDHE-ECDSA.

    aiortc ships an ECDSA-only DTLS cipher list; esp_peer presents an RSA DTLS
    certificate, so without this the handshake dies with HANDSHAKE_FAILURE before
    any media flows. Browsers already support these suites, so it's harmless for
    the web client.
    """
    try:
        from aiortc.rtcdtlstransport import RTCCertificate
    except ImportError:
        # aiortc only ships with the `realtime` extra. This patch is optional
        # hardening for the esp_peer handshake — without aiortc there's no WebRTC
        # path to harden. Unit tests enable the realtime config flag to exercise
        # the hello policy without installing the heavy extra, so degrade quietly.
        log.warning("aiortc not installed — skipping DTLS cipher broadening")
        return

    if getattr(RTCCertificate, "_vauxr_cipher_patched", False):
        return

    _orig = RTCCertificate._create_ssl_context
    _ciphers = (
        b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
        b"ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
        b"ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:"
        b"ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:"
        b"AES128-GCM-SHA256:AES128-SHA:AES256-SHA"
    )

    def _patched(self, srtp_profiles):
        ctx = _orig(self, srtp_profiles)
        ctx.set_cipher_list(_ciphers)
        return ctx

    RTCCertificate._create_ssl_context = _patched
    RTCCertificate._vauxr_cipher_patched = True
    log.info("Patched aiortc DTLS cipher list to include ECDHE-RSA (esp_peer compat)")


@transport_boundary
async def _offer_handler(request: web.Request) -> web.Response:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not isinstance(body, dict) or "sdp" not in body or "type" not in body:
        return web.json_response({"error": "Missing sdp/type"}, status=400)

    device_id = body.get("device_id")
    token = body.get("token")
    header = request.headers.get("Authorization", "")
    # Existing device body-token signaling remains scoped; conflicting credentials fail closed.
    principal = authenticate(
        (header[7:] if header.startswith("Bearer ") else None) if header else token
    )
    if (not allowed(principal, Operation.REALTIME_OFFER, resource=device_id)
            or (header and token is not None and authenticate(token) != principal)):
        audit_denial(principal is not None)
        status = 401 if principal is None else 403
        return web.json_response({"error": "unauthorized" if status == 401 else "forbidden"}, status=status)
    # A client-supplied peer handle can select another device's connection in SmallWebRTC.
    # Re-offers by pc_id are unavailable until peer ownership is enforced in the manager.
    if body.get("pc_id") is not None or body.get("restart_pc"):
        audit_denial(True)
        return web.json_response({"error": "forbidden"}, status=403)

    from realtime_session import get_manager

    manager = get_manager()
    # Identity is checked before consulting wake state or creating media resources.
    if not manager.can_accept_offer(device_id):
        log.warning("realtime offer for %s rejected — no active realtime.start", device_id)
        return web.json_response({"error": "No active realtime session"}, status=403)

    active = channel_registry.get_active()
    dependencies = []
    if active is not None and active.type == "openclaw":
        dependencies = [Principal(r.role, r.subject, r.id, r.generation) for r in get_store().records
                        if r.role == Role.INTEGRATION and r.subject == active.id and get_store().usable(r)]
        if not dependencies:
            return web.json_response({"error": "unauthorized"}, status=401)
    try:
        answer = await manager.handle_offer(device_id, body)
    except Exception:  # noqa: BLE001
        log.error("realtime offer failed")
        # End the wake the device armed on realtime.start; otherwise it can sit
        # in listening with no WebRTC path until disconnect or the next wake.
        await manager.abort_wake(device_id)
        return web.json_response({"error": "realtime offer failed"}, status=500)

    if not current(principal) or (dependencies and not any(current(p) for p in dependencies)):
        await manager.stop(device_id)
        return web.json_response({"error": "unauthorized"}, status=401)
    if answer is None:
        await manager.abort_wake(device_id)
        return web.json_response({"error": "No SDP answer"}, status=500)
    close_lock = asyncio.Lock()

    async def close_revoked() -> None:
        async with close_lock:
            if _media_authorities.get(device_id) is authorities:
                await manager.stop(device_id)
                _media_authorities.pop(device_id, None)
                for retained in authorities:
                    auth_connections.release(retained)

    for previous in _media_authorities.pop(device_id, []):
        auth_connections.release(previous)
    authorities = [auth_connections.retain(p, close_revoked) for p in (principal, *dependencies)]
    _media_authorities[device_id] = authorities
    return web.json_response(answer)


def attach_realtime_routes(app: web.Application, channel_server: Any) -> None:
    """Apply the cipher patch, configure the manager, and add the offer route."""
    broaden_aiortc_dtls_ciphers()
    from realtime_session import get_manager

    get_manager().configure(channel_server)
    cfg = get_config().realtime
    app.router.add_post(cfg.offer_path, _offer_handler)
    log.info(
        "Realtime WebRTC enabled: offer=%s esp32_mode=%s host=%r",
        cfg.offer_path,
        cfg.esp32_mode,
        cfg.host,
    )
