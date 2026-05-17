"""Entry point — device WS + channel WS + HTTP API in one aiohttp app.

Port of `src/server.ts`. The single-process design matches the Node
version: one event loop, one process, one aiohttp Application.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aiohttp import WSMsgType, web

from . import channel_registry, device_registry as registry
from .auth import validate_token
from .channel_server import ChannelServer
from .config import get_config
from .http_server import attach_http_routes, cors_middleware, serve_static
from .openclaw_client import OpenClawClient
from .pipeline import run_voice_turn
from .protocol import encode_text_message, parse_text_message

log = logging.getLogger("vauxr.server")


class ConnectionState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"


@dataclass
class ConnectionCtx:
    state: ConnectionState = ConnectionState.IDLE
    device_id: str | None = None
    audio_chunks: list[bytes] = field(default_factory=list)
    output_sample_rate: int | None = None


@dataclass
class AppState:
    openclaw_client: OpenClawClient | None = None
    channel_server: ChannelServer = field(default_factory=ChannelServer)


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

    msg_type = msg["type"]
    if msg_type == "voice.start":
        await _voice_start(state, ws, ctx, msg)
    elif msg_type == "voice.end":
        await _voice_end(state, ws, ctx)
    elif msg_type == "abort":
        _voice_abort(ctx)
    else:
        await send_json(
            ws,
            {"type": "error", "code": "UNKNOWN_MESSAGE", "message": f"Unknown type: {msg_type}"},
        )


async def _voice_start(
    state: AppState, ws: web.WebSocketResponse, ctx: ConnectionCtx, msg: dict[str, Any]
) -> None:
    device_id = msg.get("device_id")
    token = msg.get("token")
    if not isinstance(device_id, str) or not isinstance(token, str):
        await send_json(
            ws,
            {"type": "error", "code": "INVALID_MESSAGE", "message": "Missing device_id or token"},
        )
        return

    auth = validate_token(token)
    if not auth.ok:
        await send_json(
            ws,
            {"type": "error", "code": "UNAUTHORIZED", "message": auth.reason or "Invalid token"},
        )
        await ws.close()
        return

    if ctx.device_id:
        registry.abort_active_turn(ctx.device_id)

    ctx.device_id = device_id
    ctx.audio_chunks = []
    ctx.state = ConnectionState.LISTENING
    entry = registry.register(device_id, ws=ws, name=msg.get("name") or device_id)
    output_rate = (
        msg.get("output_sample_rate") or msg.get("sample_rate") or entry.config.get("output_sample_rate")
    )
    if isinstance(output_rate, (int, float)) and output_rate > 0:
        ctx.output_sample_rate = int(output_rate)
        entry.output_sample_rate = ctx.output_sample_rate

    registry.set_state(device_id, "listening")
    await send_json(ws, {"type": "ready"})


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
    chunks = ctx.audio_chunks
    total = sum(len(c) for c in chunks)
    log.info("voice.end from %s: %d chunks, %d bytes", device_id, len(chunks), total)
    ctx.audio_chunks = []

    abort = asyncio.Event()
    entry = registry.get(device_id)
    if entry is not None:
        entry.abort_event = abort

    async def _run() -> None:
        try:
            await run_voice_turn(
                device_id,
                chunks,
                ws,
                state.openclaw_client,
                state.channel_server,
                abort,
                ctx.output_sample_rate,
            )
        except Exception as e:  # noqa: BLE001
            log.error("Pipeline error for %s: %s", device_id, e)
            await send_json(
                ws, {"type": "error", "code": "PIPELINE_ERROR", "message": str(e)}
            )
        finally:
            ctx.state = ConnectionState.IDLE
            registry.set_state(device_id, "idle")
            e = registry.get(device_id)
            if e is not None:
                e.abort_event = None

    asyncio.create_task(_run())


def _voice_abort(ctx: ConnectionCtx) -> None:
    if ctx.device_id:
        registry.abort_active_turn(ctx.device_id)
        registry.set_state(ctx.device_id, "idle")
        ctx.state = ConnectionState.IDLE
        ctx.audio_chunks = []


def handle_binary(ctx: ConnectionCtx, data: bytes) -> None:
    if len(data) < 3:
        return
    msg_type = data[0]
    if msg_type == 0x01 and ctx.state == ConnectionState.LISTENING:
        ctx.audio_chunks.append(bytes(data[3:]))


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
                handle_binary(ctx, msg.data)
            elif msg.type == WSMsgType.ERROR:
                log.warning("ws error: %s", ws.exception())
    finally:
        log.info("device disconnected: %s", ctx.device_id or "unknown")
        if ctx.device_id:
            registry.abort_active_turn(ctx.device_id)
            registry.unregister(ctx.device_id)
    return ws


async def channel_ws_handler(request: web.Request) -> web.WebSocketResponse:
    state: AppState = request.app[APP_STATE]
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await state.channel_server.handle_connection(ws)
    return ws


def make_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app[APP_STATE] = AppState()
    cfg = get_config()
    app.router.add_get(cfg.channel.ws_path, channel_ws_handler)
    app.router.add_get("/ws", device_ws_handler)
    attach_http_routes(app)
    # Catch-all static fallback (serves the web-client at /). Must be last so
    # /ws, channel WS, and /api/* are matched first.
    app.router.add_get("/{tail:.*}", serve_static)
    return app


async def _startup(app: web.Application) -> None:
    state: AppState = app[APP_STATE]
    cfg = get_config()
    # Load channel registry.
    channel_registry.load()
    log.info("channel registry loaded")

    active = channel_registry.get_active()
    if cfg.openclaw.url and active is not None and active.type == "openclaw-direct":
        client = OpenClawClient()
        try:
            await client.connect()
            state.openclaw_client = client
            log.info("OpenClaw connected (openclaw-direct active channel)")
        except Exception as e:  # noqa: BLE001
            log.error("Failed to connect to OpenClaw: %s", e)
            log.error("Server will start but openclaw-direct will fail until OpenClaw reconnects")
            state.openclaw_client = client
    elif not cfg.openclaw.url:
        log.info("OPENCLAW_URL not set — openclaw-direct channel unavailable")


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
    # Single-port mode: both WS and HTTP share the WS port (matching the
    # Node version, which binds one WS server and one HTTP server). To keep
    # the existing dual-port contract, bind both ports onto the same
    # application via two TCPSites.
    loop = asyncio.new_event_loop()

    async def run() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        ws_site = web.TCPSite(runner, host="0.0.0.0", port=cfg.ws.port)
        await ws_site.start()
        http_site = web.TCPSite(runner, host="0.0.0.0", port=cfg.http.port)
        await http_site.start()
        log.info("listening on ws=%d, http=%d", cfg.ws.port, cfg.http.port)
        # Block forever — aiohttp's run_app does the same.
        while True:
            await asyncio.sleep(3600)

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
