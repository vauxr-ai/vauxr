"""Channel-plugin WS endpoint (`/channel`).

Port of `src/channel-server.ts`. Each plugin connection authenticates with
a channel token, then exchanges `channel.transcript` / `channel.response.*`
messages with the active device pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, TypedDict

from aiohttp import WSMsgType, web

import channel_registry

log = logging.getLogger("vauxr.channel_server")

_AUTH_TIMEOUT_S = 10.0


class DeviceResponseListener(TypedDict):
    on_delta: Callable[[str, str], None]
    on_end: Callable[[str], None]
    on_error: Callable[[str, str], None]


class _Connection:
    __slots__ = ("ws", "channel", "authenticated")

    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws: web.WebSocketResponse = ws
        self.channel: channel_registry.Channel | None = None
        self.authenticated = False


async def _send_json(ws: web.WebSocketResponse, obj: dict[str, Any]) -> None:
    if ws.closed:
        return
    try:
        await ws.send_str(json.dumps(obj, separators=(",", ":")))
    except (ConnectionResetError, RuntimeError):
        pass


class ChannelServer:
    def __init__(self) -> None:
        self._connections: dict[str, _Connection] = {}
        self._response_listeners: dict[str, DeviceResponseListener] = {}

    # --- Pipeline-facing API ---

    def get_active_channel(self) -> channel_registry.Channel | None:
        return channel_registry.get_active()

    def is_active_connected(self) -> bool:
        active = channel_registry.get_active()
        if active is None:
            return False
        if active.type == "openclaw-direct":
            return True
        conn = self._connections.get(active.id)
        return conn is not None and not conn.ws.closed

    def send_transcript(self, device_id: str, text: str) -> bool:
        active = channel_registry.get_active()
        if active is None:
            log.warning("No active channel — dropping transcript")
            return False
        if active.type == "openclaw-direct":
            return False
        conn = self._connections.get(active.id)
        if conn is None or conn.ws.closed:
            log.warning("Active channel %s not connected — dropping transcript", active.name)
            return False

        # Schedule the WS send on the event loop; the pipeline calls this
        # synchronously, so we return True optimistically once we've queued.
        asyncio.create_task(
            _send_json(
                conn.ws,
                {
                    "type": "channel.transcript",
                    "deviceId": device_id,
                    "sessionKey": f"vauxr:{device_id}",
                    "text": text,
                },
            )
        )
        log.info("Sent transcript to %s: %r", active.name, text)
        return True

    def add_response_listener(self, device_id: str, listener: DeviceResponseListener) -> None:
        self._response_listeners[device_id] = listener

    def remove_response_listener(self, device_id: str) -> None:
        self._response_listeners.pop(device_id, None)

    def get_response_listener(self, device_id: str) -> DeviceResponseListener | None:
        return self._response_listeners.get(device_id)

    # --- WS handler ---

    async def handle_connection(self, ws: web.WebSocketResponse) -> None:
        log.info("new channel connection")
        conn = _Connection(ws)
        auth_done = asyncio.Event()

        async def auth_timeout() -> None:
            try:
                await asyncio.wait_for(auth_done.wait(), timeout=_AUTH_TIMEOUT_S)
            except asyncio.TimeoutError:
                if not conn.authenticated:
                    await _send_json(
                        ws,
                        {"type": "error", "code": "AUTH_TIMEOUT", "message": "Authentication timeout"},
                    )
                    await ws.close()

        timeout_task = asyncio.create_task(auth_timeout())

        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    await _send_json(
                        ws,
                        {"type": "error", "code": "INVALID_MESSAGE", "message": "Invalid JSON"},
                    )
                    continue

                if not isinstance(payload, dict):
                    continue

                if not conn.authenticated:
                    if payload.get("type") == "channel.auth":
                        await self._handle_auth(conn, payload.get("token", ""))
                        auth_done.set()
                    else:
                        await _send_json(
                            ws,
                            {
                                "type": "error",
                                "code": "UNAUTHORIZED",
                                "message": "Must authenticate first",
                            },
                        )
                    continue

                await self._handle_authenticated_message(conn, payload)
        finally:
            auth_done.set()
            timeout_task.cancel()
            if conn.authenticated and conn.channel is not None:
                log.info("channel disconnected: %s (%s)", conn.channel.name, conn.channel.id)
                # Only remove if this conn is still the current one (a
                # replacement might have superseded it during auth).
                if self._connections.get(conn.channel.id) is conn:
                    self._connections.pop(conn.channel.id, None)
            else:
                log.info("unauthenticated channel connection closed")

    async def _handle_auth(self, conn: _Connection, token: str) -> None:
        channel = await channel_registry.validate_channel_token(token)
        if channel is None:
            await _send_json(
                conn.ws,
                {"type": "error", "code": "UNAUTHORIZED", "message": "Invalid channel token"},
            )
            await conn.ws.close()
            return
        conn.authenticated = True
        conn.channel = channel

        existing = self._connections.get(channel.id)
        if existing is not None and existing is not conn:
            # Kick the previous connection — store ours afterward so the
            # kicked one's close-cleanup (see handle_connection) sees a
            # different conn and doesn't clear our slot.
            await existing.ws.close()

        self._connections[channel.id] = conn
        await _send_json(
            conn.ws,
            {"type": "channel.ready", "channelId": channel.id, "name": channel.name},
        )
        log.info("channel authenticated: %s (%s)", channel.name, channel.id)

    async def _handle_authenticated_message(self, conn: _Connection, msg: dict[str, Any]) -> None:
        device_id = msg.get("deviceId")
        run_id = msg.get("runId")
        msg_type = msg.get("type")
        if not isinstance(device_id, str) or not isinstance(run_id, str):
            channel_name = conn.channel.name if conn.channel else "?"
            log.warning("%s: ignoring %s — missing deviceId or runId", channel_name, msg_type)
            return
        listener = self._response_listeners.get(device_id)
        if listener is None:
            channel_name = conn.channel.name if conn.channel else "?"
            log.warning("%s: no listener for %s (%s)", channel_name, device_id, msg_type)
            return

        if msg_type == "channel.response.delta":
            text = msg.get("text")
            if isinstance(text, str):
                listener["on_delta"](run_id, text)
        elif msg_type == "channel.response.end":
            listener["on_end"](run_id)
        elif msg_type == "channel.response.error":
            listener["on_error"](run_id, str(msg.get("message", "Channel error")))
        else:
            await _send_json(
                conn.ws,
                {"type": "error", "code": "UNKNOWN_MESSAGE", "message": f"Unknown type: {msg_type}"},
            )
