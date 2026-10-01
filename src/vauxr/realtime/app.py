"""Realtime (WebRTC/Pipecat) HTTP wiring for the vauxr aiohttp app.

Only imported when REALTIME_ENABLED=1. Adds the authenticated SmallWebRTC
`/api/offer` endpoint and applies the aiortc DTLS cipher patch required for the
firmware's esp_peer (RSA cert) to complete the DTLS handshake.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

from aiohttp import web

import vauxr.auth.connections as auth_connections
import vauxr.agents.registry as agent_registry
from vauxr.auth.service import authenticate, current, get_store
from vauxr.auth.policy import Operation, Principal, Role, allowed, audit_denial
from vauxr.config import get_config
from vauxr.web.server import transport_boundary

log = logging.getLogger("vauxr.realtime")
_media_authorities: dict[str, list[auth_connections.Connection]] = {}


def restrict_aioice_host_candidates(host: str) -> bool:
    """Gather ICE candidates only on the configured realtime interface.

    Pipecat's ESP32 SDP cleanup removes candidates that do not match ``host``,
    but aioice has already opened their UDP sockets by then. Those hidden
    sockets still send connectivity checks, which esp_peer reports as unknown
    remote ports. Restricting address discovery before peer construction keeps
    the ICE agent and the SDP answer on the same single interface.
    """
    if not host:
        return False

    try:
        import aioice.ice as aioice_ice
    except ImportError:
        log.warning("aioice not installed — skipping ICE host restriction")
        return False

    current = aioice_ice.get_host_addresses
    original = getattr(current, "_vauxr_original", current)
    try:
        resolved = {
            info[4][0]
            for info in socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        }
    except socket.gaierror as exc:
        log.warning("Could not resolve REALTIME_HOST %r for ICE restriction: %s", host, exc)
        return False

    local_ipv4 = original(use_ipv4=True, use_ipv6=False)
    selected = next((address for address in local_ipv4 if address in resolved), None)
    if selected is None:
        log.warning(
            "REALTIME_HOST %r does not resolve to a local IPv4 address; "
            "leaving aioice candidate gathering unchanged",
            host,
        )
        return False

    def _restricted(use_ipv4: bool, use_ipv6: bool) -> list[str]:
        del use_ipv6
        return [selected] if use_ipv4 else []

    _restricted._vauxr_original = original  # type: ignore[attr-defined]
    _restricted._vauxr_host = selected  # type: ignore[attr-defined]
    aioice_ice.get_host_addresses = _restricted
    log.info("Restricted aioice host candidates to %s", selected)
    return True


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

    from vauxr.realtime.session import RealtimeOfferConflict, get_manager

    manager = get_manager()
    # Identity is checked before consulting wake state or creating media resources.
    if not manager.can_accept_offer(device_id):
        log.warning("realtime offer for %s rejected — no active realtime.start", device_id)
        return web.json_response({"error": "No active realtime session"}, status=403)

    active = agent_registry.get_active()
    dependencies = []
    if active is not None and active.type == "openclaw":
        dependencies = [Principal(r.role, r.subject, r.id, r.generation) for r in get_store().records
                        if r.role == Role.INTEGRATION and r.subject == active.id and get_store().usable(r)]
        if not dependencies:
            return web.json_response({"error": "unauthorized"}, status=401)
    wake = getattr(manager, "_wake_generations", {}).get(device_id)
    try:
        answer = await manager.handle_offer(device_id, body)
    except RealtimeOfferConflict:
        return web.json_response({"error": "Realtime wake superseded or already connected"}, status=409)
    except Exception:  # noqa: BLE001
        log.error("realtime offer failed")
        # End the wake the device armed on realtime.start; otherwise it can sit
        # in listening with no WebRTC path until disconnect or the next wake.
        await manager.abort_wake(device_id, expected_wake=wake)
        return web.json_response({"error": "realtime offer failed"}, status=500)

    if not current(principal) or (dependencies and not any(current(p) for p in dependencies)):
        # Retain the failed admission too: teardown may hang or raise, and must
        # remain visible to lifecycle HTTP/maintenance until it actually finishes.
        for authority in (principal, *dependencies):
            if not current(authority):
                auth_connections.retain(authority, lambda: manager.stop(device_id))
        try:
            await auth_connections.disconnect_stale(get_store())
        except RuntimeError:
            return web.json_response({"error": "transport_teardown_unavailable"}, status=503)
        return web.json_response({"error": "unauthorized"}, status=401)
    if answer is None:
        await manager.abort_wake(device_id, expected_wake=wake)
        return web.json_response({"error": "No SDP answer"}, status=500)
    close_lock = asyncio.Lock()

    async def close_revoked() -> None:
        async with close_lock:
            if _media_authorities.get(device_id) is authorities:
                await manager.stop(device_id)
                # A replacement offer may register while stop awaits the old
                # session's cleanup. Only retire this callback's registration.
                if _media_authorities.get(device_id) is authorities:
                    _media_authorities.pop(device_id, None)
                for retained in authorities:
                    auth_connections.release(retained)

    for previous in _media_authorities.pop(device_id, []):
        auth_connections.release(previous)
    authorities = [auth_connections.retain(p, close_revoked) for p in (principal, *dependencies)]
    _media_authorities[device_id] = authorities
    return web.json_response(answer)


def attach_realtime_routes(app: web.Application, agent_server: Any) -> None:
    """Apply WebRTC compatibility patches, configure the manager, and add its route."""
    broaden_aiortc_dtls_ciphers()
    from vauxr.realtime.session import get_manager

    cfg = get_config().realtime
    restrict_aioice_host_candidates(cfg.host)
    get_manager().configure(agent_server)
    app.router.add_post(cfg.offer_path, _offer_handler)
    log.info(
        "Realtime WebRTC enabled: offer=%s esp32_mode=%s host=%r",
        cfg.offer_path,
        cfg.esp32_mode,
        cfg.host,
    )
