"""Entry point — device WS server + (later) HTTP API.

Phase 4: handshake-only WS server. Subsequent phases wire up the device
registry, pipeline, and HTTP API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aiohttp import WSMsgType, web

from .auth import validate_token
from .config import get_config
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


async def send_json(ws: web.WebSocketResponse, obj: dict[str, Any]) -> None:
    if ws.closed:
        return
    try:
        await ws.send_str(encode_text_message(obj))
    except ConnectionResetError:
        pass


async def handle_text(ws: web.WebSocketResponse, ctx: ConnectionCtx, raw: str) -> None:
    msg = parse_text_message(raw)
    if msg is None or "type" not in msg or not isinstance(msg.get("type"), str):
        await send_json(ws, {"type": "error", "code": "INVALID_MESSAGE", "message": "Invalid JSON"})
        return

    msg_type = msg["type"]
    if msg_type == "voice.start":
        await _handle_voice_start(ws, ctx, msg)
    elif msg_type == "voice.end":
        await _handle_voice_end(ws, ctx)
    elif msg_type == "abort":
        await _handle_abort(ctx)
    else:
        await send_json(
            ws,
            {"type": "error", "code": "UNKNOWN_MESSAGE", "message": f"Unknown type: {msg_type}"},
        )


async def _handle_voice_start(ws: web.WebSocketResponse, ctx: ConnectionCtx, msg: dict[str, Any]) -> None:
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

    ctx.device_id = device_id
    ctx.audio_chunks = []
    ctx.state = ConnectionState.LISTENING
    output_rate = msg.get("output_sample_rate") or msg.get("sample_rate")
    if isinstance(output_rate, (int, float)) and output_rate > 0:
        ctx.output_sample_rate = int(output_rate)
    await send_json(ws, {"type": "ready"})


async def _handle_voice_end(ws: web.WebSocketResponse, ctx: ConnectionCtx) -> None:
    if ctx.state != ConnectionState.LISTENING or ctx.device_id is None:
        await send_json(
            ws,
            {"type": "error", "code": "INVALID_STATE", "message": "Not in listening state"},
        )
        return
    # Phase 4: no pipeline yet — just transition back to idle.
    # Pipeline integration lands in Phase 11.
    ctx.state = ConnectionState.IDLE
    ctx.audio_chunks = []


async def _handle_abort(ctx: ConnectionCtx) -> None:
    # Phase 4: no active turn to abort; just reset state.
    ctx.state = ConnectionState.IDLE
    ctx.audio_chunks = []


async def device_ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ctx = ConnectionCtx()
    log.info("device connected from %s", request.remote)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await handle_text(ws, ctx, msg.data)
            elif msg.type == WSMsgType.BINARY:
                # Phase 4: audio handling lands in Phase 11.
                if ctx.state == ConnectionState.LISTENING and len(msg.data) >= 3:
                    ctx.audio_chunks.append(bytes(msg.data[3:]))
            elif msg.type == WSMsgType.ERROR:
                log.warning("ws error: %s", ws.exception())
    finally:
        log.info("device disconnected: %s", ctx.device_id or "unknown")
    return ws


def make_app() -> web.Application:
    """Build the aiohttp Application with WS routes wired up.

    HTTP routes are added in Phase 6.
    """
    app = web.Application()
    app.router.add_get("/", device_ws_handler)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = get_config()
    app = make_app()
    web.run_app(app, host="0.0.0.0", port=cfg.ws.port)


if __name__ == "__main__":
    main()
