"""OpenClaw native gateway WebSocket client.

Port of `src/openclaw-client.ts`. Connects outbound to OpenClaw, performs
the v3 connect handshake using a per-server Ed25519 identity, then sends
`chat.send` requests and routes streaming `chat` events back as deltas.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from config import get_config
from device_identity import (
    SignParams,
    get_device_token,
    load_or_create_identity,
    save_device_token,
    sign_connect_payload,
)

log = logging.getLogger("vauxr.openclaw")

_MIN_PROTO = 4
_MAX_PROTO = 4
_KEEPALIVE_S = 30.0


class _Pending:
    """A request awaiting its response.

    Optional `on_payload` runs synchronously when the response arrives —
    this lets `chat.send` install its chat listener before any further
    messages are processed, matching the Node port's nested-callback shape.
    """

    __slots__ = ("future", "on_payload")

    def __init__(
        self,
        on_payload: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self.on_payload = on_payload


class _ChatListener:
    __slots__ = ("on_delta", "future")

    def __init__(self, on_delta: Callable[[str], None]) -> None:
        self.on_delta = on_delta
        self.future: asyncio.Future[None] = asyncio.get_event_loop().create_future()


class OpenClawClient:
    """Single-connection gateway client with reconnect + chat streaming."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None

        self._req_id = 0
        self._pending: dict[str, _Pending] = {}
        self._chat: dict[str, _ChatListener] = {}

        self._connected = False
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._should_reconnect = True
        self._disconnect_handlers: list[Callable[[], Awaitable[None] | None]] = []

    # --- Public API ---

    def on_disconnect(self, handler: Callable[[], Awaitable[None] | None]) -> None:
        self._disconnect_handlers.append(handler)

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        cfg = get_config()
        url = cfg.openclaw.url
        token = cfg.openclaw.token
        data_dir = cfg.data_dir
        self._should_reconnect = True

        load_or_create_identity(data_dir)
        device_token = get_device_token(data_dir)

        session = aiohttp.ClientSession()
        try:
            ws = await session.ws_connect(url, heartbeat=None, autoping=False)
        except Exception:
            await session.close()
            raise

        self._session = session
        self._ws = ws
        log.info("WebSocket connected, waiting for challenge...")

        # Wait for the challenge → respond → first connect.res before
        # returning. The handshake is bounded by a reasonable timeout so
        # callers don't hang on a stuck server.
        handshake_done = asyncio.get_event_loop().create_future()
        # Spawn the reader task.
        self._reader_task = asyncio.create_task(self._reader_loop(handshake_done, data_dir, token, device_token))

        try:
            await asyncio.wait_for(handshake_done, timeout=30.0)
        except asyncio.TimeoutError:
            await self._teardown()
            raise

    async def chat(self, session_key: str, message: str, on_delta: Callable[[str], None]) -> None:
        if not self._connected or self._ws is None:
            raise RuntimeError("OpenClaw not connected")

        req_id = self._next_req_id()
        idem_key = f"lv-{int(time.time() * 1000)}-{uuid.uuid4()}"
        listener = _ChatListener(on_delta)

        def _install_listener(payload: dict[str, Any]) -> None:
            run_id = payload.get("runId") if isinstance(payload, dict) else None
            if not run_id:
                listener.future.set_exception(RuntimeError("No runId in chat.send response"))
                return
            # Install synchronously inside the reader's message loop so any
            # chat events that arrive immediately after the res are routed.
            self._chat[run_id] = listener

        pending = _Pending(on_payload=_install_listener)
        self._pending[req_id] = pending

        await self._ws.send_str(
            json.dumps(
                {
                    "type": "req",
                    "id": req_id,
                    "method": "chat.send",
                    "params": {"sessionKey": session_key, "message": message, "idempotencyKey": idem_key},
                }
            )
        )

        # Wait for both stages: chat.send res (which installs the listener
        # via on_payload) and the eventual final/error event.
        try:
            await pending.future
        except RuntimeError:
            # Re-raise via the listener path if not already set.
            if not listener.future.done():
                raise
        await listener.future

    async def close(self) -> None:
        self._should_reconnect = False
        await self._teardown()

    # --- Internal ---

    def _next_req_id(self) -> str:
        self._req_id = (self._req_id + 1) % 100_000
        return str(self._req_id)

    async def _teardown(self) -> None:
        self._connected = False
        for task_name in ("_keepalive_task", "_reader_task"):
            t = getattr(self, task_name)
            if t and not t.done():
                t.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None

        # Reject pending & chats.
        for p in self._pending.values():
            if not p.future.done():
                p.future.set_exception(RuntimeError("OpenClaw disconnected"))
        self._pending.clear()
        for c in self._chat.values():
            if not c.future.done():
                c.future.set_exception(RuntimeError("OpenClaw disconnected"))
        self._chat.clear()

    async def _keepalive(self) -> None:
        try:
            while True:
                await asyncio.sleep(_KEEPALIVE_S)
                if self._ws is not None and not self._ws.closed:
                    try:
                        await self._ws.ping()
                    except (ConnectionResetError, RuntimeError):
                        return
        except asyncio.CancelledError:
            return

    async def _reader_loop(
        self,
        handshake_done: asyncio.Future[None],
        data_dir: str,
        token: str,
        prior_device_token: str | None,
    ) -> None:
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                await self._handle_message(data, handshake_done, data_dir, token, prior_device_token)
        except (aiohttp.ClientError, asyncio.CancelledError):
            pass
        finally:
            self._connected = False
            log.info("Disconnected")
            for h in self._disconnect_handlers:
                result = h()
                if asyncio.iscoroutine(result):
                    try:
                        await result
                    except Exception as e:  # noqa: BLE001
                        log.warning("disconnect handler error: %s", e)

            for p in self._pending.values():
                if not p.future.done():
                    p.future.set_exception(RuntimeError("OpenClaw disconnected"))
            self._pending.clear()
            for c in self._chat.values():
                if not c.future.done():
                    c.future.set_exception(RuntimeError("OpenClaw disconnected"))
            self._chat.clear()

            if not handshake_done.done():
                handshake_done.set_exception(RuntimeError("OpenClaw disconnected during handshake"))

            if self._should_reconnect:
                # Spawn a reconnect task (without recursing inside the reader).
                delay = self._reconnect_delay
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                log.info("Reconnecting in %.0fms...", delay * 1000)
                if self._reconnect_task and not self._reconnect_task.done():
                    return
                self._reconnect_task = asyncio.create_task(self._reconnect_after(delay))

    async def _reconnect_after(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if not self._should_reconnect:
            return
        try:
            await self.connect()
        except Exception as e:  # noqa: BLE001
            log.error("Reconnect failed: %s", e)

    async def _handle_message(
        self,
        msg: dict[str, Any],
        handshake_done: asyncio.Future[None],
        data_dir: str,
        token: str,
        prior_device_token: str | None,
    ) -> None:
        msg_type = msg.get("type")

        # Challenge → send connect.
        if msg_type == "event" and msg.get("event") == "connect.challenge":
            payload = msg.get("payload") or {}
            nonce = payload.get("nonce") if isinstance(payload, dict) else None
            if not isinstance(nonce, str):
                return
            req_id = self._next_req_id()
            self._pending[req_id] = _Pending()
            assert self._ws is not None
            await self._ws.send_str(json.dumps(self._build_connect_msg(req_id, nonce, data_dir, token, prior_device_token)))

            async def _await_connect() -> None:
                try:
                    payload = await self._pending[req_id].future
                except RuntimeError as err:
                    err_msg = str(err)
                    if "pairing required" in err_msg.lower() or "PAIRING_REQUIRED" in err_msg:
                        log.info("Device not yet paired — approve in OpenClaw UI")
                        if not handshake_done.done():
                            handshake_done.set_result(None)
                        return
                    if not handshake_done.done():
                        handshake_done.set_exception(err)
                    return

                self._connected = True
                self._reconnect_delay = 1.0
                self._keepalive_task = asyncio.create_task(self._keepalive())

                auth_info = payload.get("auth") if isinstance(payload, dict) else None
                if isinstance(auth_info, dict):
                    scopes = auth_info.get("scopes")
                    if isinstance(scopes, list):
                        log.info("Granted scopes: %s", ", ".join(scopes))
                    new_token = auth_info.get("deviceToken")
                    if isinstance(new_token, str) and new_token != prior_device_token:
                        save_device_token(data_dir, new_token)
                        log.info("New device token issued and saved")

                if not handshake_done.done():
                    handshake_done.set_result(None)

            asyncio.create_task(_await_connect())
            return

        # Response to a request.
        if msg_type == "res" and msg.get("id") is not None:
            req_id = str(msg["id"])
            pending = self._pending.pop(req_id, None)
            if pending is None:
                return
            if msg.get("ok") is False:
                err_payload = msg.get("error") or {}
                err_msg = err_payload.get("message") if isinstance(err_payload, dict) else None
                pending.future.set_exception(RuntimeError(f"OpenClaw error: {err_msg or json.dumps(msg)}"))
            else:
                payload_raw = msg.get("payload") or {}
                payload = payload_raw if isinstance(payload_raw, dict) else {}
                # Invoke the synchronous hook BEFORE setting the future so the
                # caller (and any subsequent messages already buffered by the
                # reader) see the side effects.
                if pending.on_payload is not None:
                    try:
                        pending.on_payload(payload)
                    except Exception as e:  # noqa: BLE001
                        pending.future.set_exception(e)
                        return
                pending.future.set_result(payload)
            return

        # Chat streaming events.
        if msg_type == "event" and msg.get("event") == "chat":
            payload = msg.get("payload") or {}
            if not isinstance(payload, dict):
                return
            state = payload.get("state")
            run_id = payload.get("runId")
            if not isinstance(run_id, str):
                return
            listener = self._chat.get(run_id)
            if listener is None:
                return

            if state == "delta":
                content = (payload.get("message") or {}).get("content")
                text = ""
                if isinstance(content, list) and content:
                    first = content[0]
                    if isinstance(first, dict) and isinstance(first.get("text"), str):
                        text = first["text"]
                if text:
                    listener.on_delta(text)
            elif state == "final":
                self._chat.pop(run_id, None)
                if not listener.future.done():
                    listener.future.set_result(None)
            elif state == "error":
                self._chat.pop(run_id, None)
                err = payload.get("errorMessage") or "Chat error"
                if not listener.future.done():
                    listener.future.set_exception(RuntimeError(str(err)))
            elif state == "aborted":
                self._chat.pop(run_id, None)
                if not listener.future.done():
                    listener.future.set_result(None)
            return

        # tick / health — no-op.
        if msg_type == "event" and msg.get("event") in ("tick", "health"):
            return

    def _build_connect_msg(
        self,
        req_id: str,
        nonce: str,
        data_dir: str,
        token: str,
        device_token: str | None,
    ) -> dict[str, Any]:
        identity = load_or_create_identity(data_dir)
        client_id = "gateway-client"
        client_mode = "backend"
        role = "operator"
        scopes = ["operator.read", "operator.write"]
        auth_token = device_token or token

        # Platform stays "node" to match the wire behavior of src/openclaw-client.ts.
        # OpenClaw treats this as an opaque identifier; changing it after the
        # rewrite would diverge from what the Node port has always sent and is
        # explicitly out of scope for parity work.
        signed = sign_connect_payload(
            data_dir,
            SignParams(
                nonce=nonce,
                token=auth_token,
                client_id=client_id,
                client_mode=client_mode,
                role=role,
                scopes=scopes,
                platform="node",
                device_family="",
            ),
        )

        return {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {
                "minProtocol": _MIN_PROTO,
                "maxProtocol": _MAX_PROTO,
                "client": {
                    "id": client_id,
                    "displayName": "Vauxr",
                    "version": "0.1.0",
                    "platform": "node",
                    "mode": client_mode,
                },
                "role": role,
                "scopes": scopes,
                "caps": ["tool-events"],
                "auth": {"deviceToken": device_token} if device_token else {"token": token},
                "device": {
                    "id": identity["identity"]["fingerprint"],
                    "publicKey": identity["identity"]["publicKeyRaw"],
                    "signature": signed.signature,
                    "signedAt": signed.signed_at,
                    "nonce": nonce,
                },
            },
        }
