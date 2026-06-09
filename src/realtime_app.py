"""Realtime (WebRTC/Pipecat) HTTP wiring for the vauxr aiohttp app.

Only imported when REALTIME_ENABLED=1. Adds the authenticated SmallWebRTC
`/api/offer` endpoint and applies the aiortc DTLS cipher patch required for the
firmware's esp_peer (RSA cert) to complete the DTLS handshake.
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from auth import validate_token
from config import get_config

log = logging.getLogger("vauxr.realtime")


def broaden_aiortc_dtls_ciphers() -> None:
    """Let aiortc accept ECDHE-RSA DTLS suites, not just ECDHE-ECDSA.

    aiortc ships an ECDSA-only DTLS cipher list; esp_peer presents an RSA DTLS
    certificate, so without this the handshake dies with HANDSHAKE_FAILURE before
    any media flows. Browsers already support these suites, so it's harmless for
    the web client.
    """
    from aiortc.rtcdtlstransport import RTCCertificate

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


async def _offer_handler(request: web.Request) -> web.Response:
    try:
        body: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "Invalid JSON"}, status=400)

    if not isinstance(body, dict) or "sdp" not in body or "type" not in body:
        return web.json_response({"error": "Missing sdp/type"}, status=400)

    device_id = body.get("device_id")
    token = body.get("token")
    if not isinstance(device_id, str) or not isinstance(token, str):
        return web.json_response({"error": "Missing device_id/token"}, status=400)
    if not validate_token(token).ok:
        return web.json_response({"error": "Unauthorized"}, status=401)

    from realtime_session import get_manager

    manager = get_manager()
    # The shared device token authenticates the *caller*, not which device id it
    # may claim. Require that this id actually has an armed wake (or a live
    # session for re-offers) so a token holder can't bind WebRTC to someone
    # else's pre-roll/control relay.
    if not manager.can_accept_offer(device_id):
        log.warning("realtime offer for %s rejected — no active realtime.start", device_id)
        return web.json_response({"error": "No active realtime session"}, status=403)

    try:
        answer = await manager.handle_offer(device_id, body)
    except Exception as e:  # noqa: BLE001
        log.error("realtime offer failed for %s: %s", device_id, e)
        return web.json_response({"error": str(e)}, status=500)

    if answer is None:
        return web.json_response({"error": "No SDP answer"}, status=500)
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
