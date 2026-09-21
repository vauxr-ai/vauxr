"""Entry point — device WS + agent WS + HTTP API in one aiohttp app.

Port of `src/server.ts`. The single-process design matches the Node
version: one event loop, one process, one aiohttp Application.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aiohttp import WSMsgType, web

import vauxr.auth.connections as auth_connections
import vauxr.agents.registry as agent_registry
import vauxr.devices.registry as registry
from vauxr.auth.service import authenticate, current, get_store
from vauxr.auth.policy import WS_OPERATIONS, Operation, Principal, allowed, audit_denial
from vauxr.agents.server import AgentServer
from vauxr.config import get_config
from vauxr.devices.config import pipeline_mode
from vauxr.devices.settings import realtime_policy_extras
from vauxr.web.server import (
    attach_http_routes,
    cors_middleware,
    policy_middleware,
    serve_static,
    transport_boundary,
)
from vauxr.agents.openclaw import OpenClawClient
from vauxr.web.owner import owner_middleware
from vauxr.pipeline import run_voice_turn
from vauxr.protocol import encode_text_message, parse_text_message
from vauxr.speech.store import Selection, resolve
from vauxr.speech.store import get_store as get_speech_store

log = logging.getLogger("vauxr.server")


class ConnectionState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"


@dataclass
class ConnectionCtx:
    state: ConnectionState = ConnectionState.IDLE
    device_id: str | None = None
    principal: Principal | None = None
    authority: auth_connections.Connection | None = None
    audio_chunks: list[bytes] = field(default_factory=list)
    output_sample_rate: int | None = None
    speech_selection: Selection | None = None
    # Only acknowledged Standard playback permits a device Live handoff.
    realtime: bool = False
    realtime_media: bool = False
    turn_id: int = 0
    playback_pending: int | None = None
    handoff_ready: bool = False


@dataclass
class AppState:
    openclaw_client: OpenClawClient | None = None
    agent_server: AgentServer = field(default_factory=AgentServer)


APP_STATE: web.AppKey[AppState] = web.AppKey("state", AppState)


# --- WS helpers ---


async def send_json(ws: web.WebSocketResponse, obj: dict[str, Any]) -> None:
    if ws.closed:
        return
    try:
        await ws.send_str(encode_text_message(obj))
    except ConnectionResetError:
        pass


async def handle_text(
    state: AppState, ws: web.WebSocketResponse, ctx: ConnectionCtx, raw: str
) -> None:
    msg = parse_text_message(raw)
    if msg is None or not isinstance(msg.get("type"), str):
        await send_json(ws, {"type": "error", "code": "INVALID_MESSAGE", "message": "Invalid JSON"})
        return

    if not await _authorize_message(ws, ctx, msg):
        return
    msg_type = msg["type"]
    if ctx.realtime_media and msg_type in {"voice.start", "voice.end"}:
        from vauxr.realtime.session import get_manager
        if not get_manager().has_live_session(ctx.device_id):
            await _realtime_stop(ctx)
    if msg_type == "hello":
        await _hello(ws, ctx, msg)
    elif msg_type == "voice.start":
        # Turn-based capture doesn't apply mid-realtime: running it on the same WS
        # would clobber the agent response listener and force registry state to
        # idle while WebRTC is still up. (A device using realtime won't send this;
        # this is a guard against a stale/confused client.)
        if ctx.realtime_media:
            log.warning("ignoring voice.start during realtime session: %s", ctx.device_id)
        else:
            await _voice_start(state, ws, ctx, msg)
    elif msg_type == "voice.end":
        if not ctx.realtime_media:
            await _voice_end(state, ws, ctx)
    elif msg_type == "abort":
        # End both the Standard turn and any initializing or active peer.
        if ctx.realtime:
            await _realtime_stop(ctx)
        else:
            _voice_abort(ctx)
    elif msg_type == "realtime.start":
        await _realtime_start(state, ws, ctx, msg)
    elif msg_type == "realtime.media_ready":
        if ctx.handoff_ready and type(msg.get("turn_id")) is int and msg["turn_id"] == ctx.turn_id:
            from vauxr.realtime.session import get_manager
            ctx.realtime_media = get_manager().activate_handoff(ctx.device_id)
            if ctx.realtime_media:
                ctx.handoff_ready = False
    elif msg_type == "audio.playback_complete":
        if (type(msg.get("turn_id")) is int and msg["turn_id"] == ctx.playback_pending
                and ctx.state == ConnectionState.IDLE and ctx.realtime):
            from vauxr.realtime.session import get_manager
            if get_manager().prepare_handoff(ctx.device_id):
                ctx.handoff_ready = True
                ctx.playback_pending = None
                await send_json(ws, {"type": "realtime.handoff", "turn_id": ctx.turn_id})
    elif msg_type == "realtime.pause":
        _realtime_pause(ctx)
    elif msg_type == "realtime.resume":
        _realtime_resume(ctx)
    elif msg_type == "realtime.stop":
        await _realtime_stop(ctx)
    elif msg_type == "device.button":
        await _device_button(state, ctx, msg)
    else:
        await send_json(
            ws,
            {"type": "error", "code": "UNKNOWN_MESSAGE", "message": f"Unknown type: {msg_type}"},
        )


async def _authorize_message(ws: web.WebSocketResponse, ctx: ConnectionCtx, msg: dict[str, Any]) -> bool:
    operation = WS_OPERATIONS.get(msg.get("type"))
    principal = authenticate(msg.get("token")) if "token" in msg else ctx.principal
    resource = msg.get("device_id", ctx.device_id)
    authenticated = current(principal)
    valid = (authenticated and allowed(principal, operation, resource=resource)
             and (ctx.principal is None or principal == ctx.principal)
             and (ctx.device_id is None or resource == ctx.device_id))
    if ctx.principal is not None:
        live = registry.get(ctx.device_id)
        valid = valid and live is not None and live.ws is ws
    if not valid:
        audit_denial(authenticated)
        await send_json(ws, {"type": "error", "code": "FORBIDDEN" if authenticated else "UNAUTHORIZED",
                             "message": "Access denied"})
        await ws.close()
        return False
    if ctx.principal is None:
        if msg.get("type") not in {"hello", "voice.start", "realtime.start"}:
            audit_denial(True)
            await send_json(ws, {"type": "error", "code": "FORBIDDEN", "message": "Access denied"})
            await ws.close()
            return False
        ctx.principal = principal
        ctx.device_id = resource

        teardown: list[auth_connections.Teardown] = []

        async def close_revoked() -> None:
            if not teardown:
                teardown.append(auth_connections.Teardown(ws.close))
                live = registry.get(ctx.device_id)
                if live is None or live.ws is ws:
                    registry.abort_active_turn(ctx.device_id)
                    registry.unregister(ctx.device_id, ws)
                    if ctx.realtime:
                        from vauxr.realtime.session import get_manager

                        teardown.append(auth_connections.Teardown(lambda: get_manager().stop(ctx.device_id)))
            await auth_connections.run_teardowns(teardown)

        ctx.authority = auth_connections.retain(principal, close_revoked)
    return True


async def _hello(ws: web.WebSocketResponse, ctx: ConnectionCtx, msg: dict[str, Any]) -> None:
    """Boot-time handshake: device advertises capabilities, server returns policy.

    The device is intentionally dumb — whether realtime is enabled, which
    transport to use, and the WebRTC endpoints are all decided here (server-side)
    from config gated by the device's advertised caps.
    """
    caps = msg.get("caps")
    caps_list = [c for c in caps if isinstance(c, str)] if isinstance(caps, list) else []
    rt = get_config().realtime
    # WebRTC needs an absolute offer URL at the device's trusted signaling origin
    # (firmware only arms realtime when offer_url shares that same origin — see
    # applyHelloPolicy's same-origin credential guard) and reliable ICE host
    # munging (esp32 mode, driven separately by REALTIME_HOST). Without
    # REALTIME_HOST, ICE is unreliable, so fall back to ws rather than advertise
    # a broken policy.
    mode = pipeline_mode(registry.get_config_for(ctx.device_id or ""))
    webrtc_ok = mode == "realtime" and rt.enabled and "webrtc" in caps_list and bool(rt.host)
    if rt.enabled and "webrtc" in caps_list and not rt.host:
        log.warning(
            "realtime: %s is webrtc-capable but REALTIME_HOST is unset — falling back to ws",
            msg.get("device_id"),
        )

    device_key = ctx.device_id or ""

    # Register on hello so an idle device is visible/commandable (OTA, reboot)
    # without waiting for the first voice turn.
    if device_key:
        ctx.device_id = device_key
        registry.register(device_key, ws=ws)
        platform = msg.get("platform") if isinstance(msg.get("platform"), str) else None
        fw_version = msg.get("fw_version") if isinstance(msg.get("fw_version"), str) else None
        registry.set_hello_info(device_key, platform=platform, fw_version=fw_version)
        rate = registry.apply_output_sample_rate(device_key, msg, platform=platform)
        if rate is not None:
            ctx.output_sample_rate = rate

    realtime_policy: dict[str, Any] = {"enabled": False, "transport": "ws"}
    if webrtc_ok:
        # offer_url must be same-origin with the device's trusted signaling
        # origin (owner_auth.configured_origin) so firmware's credential guard
        # accepts it — REALTIME_HOST is for ICE candidate host munging only and
        # must never appear in this URL.
        from vauxr.auth.owner import configured_origin
        offer_url = configured_origin() + rt.offer_path
        realtime_policy = {
            "enabled": True,
            "transport": "webrtc",
            "offer_url": offer_url,
            "stun": rt.stun_url,
            "handoff": "standard_playback_v1",
            "voice_source": "live_voice_settings",
            **realtime_policy_extras(device_key),
        }

    log.info(
        "hello from %s (platform=%s caps=%s) -> realtime=%s",
        msg.get("device_id"),
        msg.get("platform"),
        caps_list,
        realtime_policy.get("transport"),
    )
    await send_json(ws, {"type": "hello", "pipeline_mode": mode, "realtime": realtime_policy})


async def _device_button(state: AppState, ctx: ConnectionCtx, msg: dict[str, Any]) -> None:
    # Only the device_id bound by an authenticated hello / voice.start /
    # realtime.start. Never trust msg.device_id — an unauthenticated socket
    # must not fire another device's webhook, mute, reboot, or prompt.
    device_id = ctx.device_id
    if not isinstance(device_id, str) or not device_id:
        log.warning("device.button ignored (no authenticated hello)")
        return
    button = msg.get("button") if isinstance(msg.get("button"), str) else "action"
    gesture = msg.get("gesture") if isinstance(msg.get("gesture"), str) else ""
    from vauxr.buttons import handle_device_button

    asyncio.create_task(
        handle_device_button(
            device_id=device_id,
            button=button,
            gesture=gesture,
            openclaw_client=state.openclaw_client,
            agent_server=state.agent_server,
        )
    )


async def _voice_start(
    state: AppState, ws: web.WebSocketResponse, ctx: ConnectionCtx, msg: dict[str, Any]
) -> None:
    device_id = ctx.device_id
    if ctx.device_id:
        registry.abort_active_turn(ctx.device_id)

    from vauxr.realtime.session import get_manager
    manager = get_manager()
    if device_id in manager._handoff_devices:
        await manager.stop(device_id)
    ctx.device_id = device_id
    ctx.turn_id += 1
    ctx.playback_pending = None
    ctx.handoff_ready = False
    ctx.audio_chunks = []
    try:
        ctx.speech_selection = resolve(device_id)
    except (KeyError, ValueError):
        await send_json(ws, {"type": "error", "code": "SPEECH_UNAVAILABLE",
                             "message": "Selected speech provider is not configured"})
        ctx.state = ConnectionState.IDLE
        return
    ctx.state = ConnectionState.LISTENING
    registry.register(device_id, ws=ws, name=msg.get("name"))
    rate = registry.apply_output_sample_rate(device_id, msg)
    if rate is not None:
        ctx.output_sample_rate = rate

    registry.set_state(device_id, "listening")
    await send_json(ws, {"type": "ready"})


@dataclass
class _VoiceTurnOutput:
    """Fence delayed pipeline output at the socket boundary after cancellation.

    Provider callbacks can finish after abort, including scheduled audio.start
    and error sends. They must never address the replacement turn's browser.
    """

    ws: web.WebSocketResponse
    abort: asyncio.Event
    device_id: str
    completed: bool = False
    failed: bool = False

    def turn_completed(self) -> None:
        self.completed = True

    @property
    def closed(self) -> bool:
        entry = registry.get(self.device_id)
        return (self.abort.is_set() or self.ws.closed or entry is None
                or entry.ws is not self.ws or entry.abort_event is not self.abort)

    async def send_str(self, data: str) -> None:
        message = parse_text_message(data)
        if message and message.get("type") == "error":
            self.failed = True
        if not self.closed:
            await self.ws.send_str(data)

    async def send_bytes(self, data: bytes) -> None:
        if not self.closed:
            await self.ws.send_bytes(data)


async def _voice_end(state: AppState, ws: web.WebSocketResponse, ctx: ConnectionCtx) -> None:
    if ctx.state != ConnectionState.LISTENING or ctx.device_id is None:
        await send_json(
            ws,
            {"type": "error", "code": "INVALID_STATE", "message": "Not in listening state"},
        )
        return

    ctx.state = ConnectionState.PROCESSING
    registry.set_state(ctx.device_id, "processing")
    device_id = ctx.device_id
    turn_id = ctx.turn_id
    selection = ctx.speech_selection
    chunks = ctx.audio_chunks
    total = sum(len(c) for c in chunks)
    log.info("voice.end from %s: %d chunks, %d bytes", device_id, len(chunks), total)
    ctx.audio_chunks = []

    abort = asyncio.Event()
    entry = registry.get(device_id)
    if entry is not None:
        entry.abort_event = abort

    async def _run() -> None:
        output = _VoiceTurnOutput(ws, abort, device_id)
        try:
            await run_voice_turn(
                device_id,
                chunks,
                output,
                state.openclaw_client,
                state.agent_server,
                abort,
                ctx.output_sample_rate,
                selection=selection,
            )
            if (not abort.is_set() and ctx.turn_id == turn_id and ctx.realtime
                    and output.completed and not output.failed and not output.closed):
                ctx.state = ConnectionState.IDLE
                ctx.playback_pending = turn_id
                await send_json(ws, {"type": "audio.playback_pending", "turn_id": turn_id})
        except Exception:  # noqa: BLE001
            log.error("Pipeline error")
            if not abort.is_set():
                await send_json(
                    ws, {"type": "error", "code": "PIPELINE_ERROR", "message": "Pipeline error"}
                )
        finally:
            # Only the turn that still owns this connection may clear it.
            # An aborted turn can finish after a replacement has started.
            e = registry.get(device_id)
            if e is entry and e is not None and e.abort_event is abort:
                ctx.state = ConnectionState.IDLE
                registry.set_state(device_id, "idle")
                e.abort_event = None

    asyncio.create_task(_run())


def _voice_abort(ctx: ConnectionCtx) -> None:
    ctx.turn_id += 1
    ctx.playback_pending = None
    ctx.handoff_ready = False
    if ctx.device_id:
        registry.abort_active_turn(ctx.device_id)
        registry.set_state(ctx.device_id, "idle")
        ctx.state = ConnectionState.IDLE
        ctx.audio_chunks = []


async def _realtime_start(
    state: AppState, ws: web.WebSocketResponse, ctx: ConnectionCtx, msg: dict[str, Any]
) -> None:
    """Keep explicit Talk Live; devices start with one complete Standard turn."""
    device_id = ctx.device_id
    # Realtime must actually be reachable server-side before we arm pre-roll: the
    # /api/offer endpoint only exists when REALTIME_ENABLED=1 and REALTIME_HOST is
    # set (same gate _hello uses to advertise the webrtc policy). Arming otherwise
    # would strand the device in "listening" with no WebRTC path — the first
    # utterance gets buffered and never processed until it disconnects.
    rt = get_config().realtime
    if not (rt.enabled and rt.host and (msg.get("mode") == "live"
            or pipeline_mode(registry.get_config_for(device_id)) == "realtime")):
        await send_json(
            ws,
            {
                "type": "error",
                "code": "REALTIME_UNAVAILABLE",
                "message": "Realtime transport is not enabled",
            },
        )
        log.warning(
            "realtime.start from %s rejected — realtime unavailable (enabled=%s host=%r)",
            device_id,
            rt.enabled,
            bool(rt.host),
        )
        return

    ctx.device_id = device_id
    ctx.realtime = True
    ctx.realtime_media = False
    registry.register(device_id, ws=ws, name=msg.get("name"))
    rate = registry.apply_output_sample_rate(device_id, msg)
    if rate is not None:
        ctx.output_sample_rate = rate

    from vauxr.realtime.session import get_manager

    manager = get_manager()
    if manager.has_live_session(device_id) and device_id not in manager._handoff_devices:
        ctx.realtime_media = True
        # Warm re-wake: peer + Pipecat session stay alive; Silero end-points the
        # turn on the WebRTC track — no WS pre-roll or device-VAD marker.
        registry.set_state(device_id, "listening")
        await send_json(ws, {"type": "ready"})
        log.info("realtime.start from %s — warm re-wake on live session", device_id)
        return

    # Firmware never sends `mode` on realtime.start (see vauxr_client.cpp
    # sendRealtimeStart) — it only exists as an explicit Talk Live trigger from
    # non-firmware clients (web-client). A device persisted as pipeline_mode
    # realtime must still route to GPT-Live on its own, or it silently falls
    # through to the legacy Standard-then-AgentLLM WebRTC path below.
    persisted_live = pipeline_mode(registry.get_config_for(device_id)) == "realtime"
    explicit_live = msg.get("mode") == "live"
    if explicit_live or persisted_live:
        import os
        from vauxr.speech.store import get_store as speech_store
        if speech_store().voice_settings(device_id)["mode"] != "realtime" or not os.environ.get("OPENAI_API_KEY"):
            ctx.realtime = False
            await send_json(ws, {"type": "error", "code": "REALTIME_UNAVAILABLE",
                                "message": "Select Realtime in speech settings and configure the server OpenAI key"})
            return
        await manager.stop(device_id)
        if not current(ctx.principal):
            await ws.close()
            return
        manager._live_devices.add(device_id)
        if explicit_live:
            # Browser Talk Live has no Standard opening turn; admit its offer now.
            ctx.realtime_media = True
            registry.set_state(device_id, "listening")
            await send_json(ws, {"type": "realtime.armed"})
            return

    # Firmware omits `mode`: run one complete Standard opening turn while the
    # GPT-Live selection is armed in _live_devices. The device sends playback
    # receipt -> realtime.handoff -> offer, and start_live() then selects GPT-Live.
    ctx.realtime_media = False
    if ctx.state == ConnectionState.IDLE:
        await _voice_start(state, ws, ctx, msg)


def _realtime_pause(ctx: ConnectionCtx) -> None:
    """Device entered warm-quiet — ignore inbound mic until the next wake."""
    if not ctx.device_id:
        return
    from vauxr.realtime.session import get_manager

    get_manager().set_mic_paused(ctx.device_id, True)
    log.info("realtime.pause from %s", ctx.device_id)


def _realtime_resume(ctx: ConnectionCtx) -> None:
    """Wake word on a warm peer — accept inbound mic again."""
    if not ctx.device_id:
        return
    from vauxr.realtime.session import get_manager

    get_manager().set_mic_paused(ctx.device_id, False)
    log.info("realtime.resume from %s", ctx.device_id)


async def _realtime_stop(ctx: ConnectionCtx) -> None:
    if not ctx.device_id:
        return
    from vauxr.realtime.session import get_manager

    _voice_abort(ctx)
    await get_manager().stop(ctx.device_id)
    ctx.realtime = False
    ctx.realtime_media = False


def handle_binary(ctx: ConnectionCtx, data: bytes) -> None:
    if (not current(ctx.principal)
            or not allowed(ctx.principal, Operation.DEVICE_AUDIO, resource=ctx.device_id)):
        audit_denial(ctx.principal is not None)
        return
    if len(data) < 3:
        return
    msg_type = data[0]
    if msg_type != 0x01:
        return
    payload = bytes(data[3:])
    if ctx.state == ConnectionState.LISTENING and not ctx.realtime_media:
        ctx.audio_chunks.append(payload)


@transport_boundary
async def device_ws_handler(request: web.Request) -> web.WebSocketResponse:
    state: AppState = request.app[APP_STATE]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ctx = ConnectionCtx()
    log.info("device connected from %s", request.remote)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await handle_text(state, ws, ctx, msg.data)
            elif msg.type == WSMsgType.BINARY:
                live = registry.get(ctx.device_id) if ctx.device_id else None
                if live is None or live.ws is not ws or not current(ctx.principal):
                    audit_denial(ctx.principal is not None)
                    await ws.close()
                    break
                handle_binary(ctx, msg.data)
            elif msg.type == WSMsgType.ERROR:
                log.warning("ws error: %s", ws.exception())
    finally:
        auth_connections.release(ctx.authority)
        log.info("device disconnected: %s", ctx.device_id or "unknown")
        if ctx.device_id:
            live = registry.get(ctx.device_id)
            # Skip if a newer hello already owns this device_id (OTA reboot).
            if live is None or live.ws is ws:
                registry.abort_active_turn(ctx.device_id)
                if ctx.realtime:
                    from vauxr.realtime.session import get_manager

                    await get_manager().stop(ctx.device_id)
                registry.unregister(ctx.device_id, ws)
    return ws


@transport_boundary
async def agent_ws_handler(request: web.Request) -> web.WebSocketResponse:
    from vauxr.web.owner import ORIGIN, secure_request

    origin = request.app[ORIGIN]
    if (request.query_string or len(request.headers.getall("Origin", [])) > 1
            or request.headers.get("Origin", origin) != origin
            or (origin.startswith("https://") and not secure_request(request))):
        raise web.HTTPForbidden()
    state: AppState = request.app[APP_STATE]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await state.agent_server.handle_connection(ws)
    return ws


def make_app() -> web.Application:
    app = web.Application(middlewares=[owner_middleware, cors_middleware, policy_middleware])
    app[APP_STATE] = AppState()
    cfg = get_config()
    app.router.add_get(cfg.agent.ws_path, agent_ws_handler)
    app.router.add_get("/ws", device_ws_handler)
    attach_http_routes(app)
    if cfg.realtime.enabled:
        # Realtime WebRTC (Pipecat) runs in-process so it can reuse agent
        # routing, the device registry, and the WS control channel. Imported
        # lazily so the pipecat/aiortc dependency is only required when enabled.
        from vauxr.realtime.app import attach_realtime_routes

        attach_realtime_routes(app, app[APP_STATE].agent_server)
    # Catch-all static fallback (serves the web-client at /). Must be last so
    # /ws, agent WS, and /api/* are matched first.
    app.router.add_get("/{tail:.*}", serve_static)
    return app


async def _startup(app: web.Application) -> None:
    state: AppState = app[APP_STATE]
    cfg = get_config()
    get_store().load()
    get_speech_store()  # Validate the speech registry before accepting turns.
    # Load agent registry.
    agent_registry.load()
    log.info("agent registry loaded")
    import vauxr.web.webhooks as webhooks

    webhooks.load()
    log.info("webhooks loaded")

    active = agent_registry.get_active()
    if cfg.openclaw.url and active is not None and active.type == "openclaw-direct":
        client = OpenClawClient()
        try:
            await client.connect()
            state.openclaw_client = client
            log.info("OpenClaw connected (openclaw-direct active agent)")
        except Exception as e:  # noqa: BLE001
            log.error("Failed to connect to OpenClaw: %s", e)
            log.error("Server will start but openclaw-direct will fail until OpenClaw reconnects")
            state.openclaw_client = client
    elif not cfg.openclaw.url:
        log.info("OPENCLAW_URL not set — openclaw-direct agent unavailable")


async def _cleanup(app: web.Application) -> None:
    state: AppState = app[APP_STATE]
    if state.openclaw_client is not None:
        await state.openclaw_client.close()


def main() -> None:
    try:
        import uvloop

        uvloop.install()
    except ImportError:  # pragma: no cover - uvloop not available on win32
        pass

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    cfg = get_config()
    log.info("Starting Vauxr WS+HTTP server on ws=%d http=%d", cfg.ws.port, cfg.http.port)

    app = make_app()
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    try:
        asyncio.run(run_server(app))
    except asyncio.CancelledError:
        pass  # SIGINT/SIGTERM cancel startup as well as the running service.


async def run_server(app: web.Application) -> None:
    """Prepare TLS before accepting traffic and reap workers on every exit path."""
    from vauxr.tls import TLSService

    cfg = get_config()
    tls = TLSService(cfg.tls) if cfg.tls.enabled else None
    runner = web.AppRunner(app, access_log=None)
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    signals = (signal.SIGINT, signal.SIGTERM)
    for sig in signals:
        loop.add_signal_handler(sig, task.cancel)
    try:
        if tls:
            await tls.prepare()
        await runner.setup()
        # Preserve the existing HTTP and device listeners on the same app.
        await web.TCPSite(runner, host="0.0.0.0", port=cfg.ws.port).start()
        await web.TCPSite(runner, host="0.0.0.0", port=cfg.http.port).start()
        if tls:
            await web.TCPSite(runner, host="0.0.0.0", port=cfg.tls.port,
                              ssl_context=tls.context.listener).start()
            tls.start()
            log.info("HTTPS/WSS listening on port %d", cfg.tls.port)
        log.info("listening on ws=%d, http=%d", cfg.ws.port, cfg.http.port)
        await asyncio.Event().wait()
    finally:
        try:
            if tls:
                await tls.close()
        finally:
            try:
                await runner.cleanup()
            finally:
                for sig in signals:
                    loop.remove_signal_handler(sig)


if __name__ == "__main__":
    main()
