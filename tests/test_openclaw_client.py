"""Phase 9: OpenClaw gateway client.

Tests run against a local fake gateway implemented with aiohttp.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web

from vauxr import config as cfg_mod, device_identity as ident
from openclaw_client import OpenClawClient


# --- Fake gateway plumbing ---


class FakeGateway:
    """Spins up an aiohttp WS server that mimics the OpenClaw gateway protocol."""

    def __init__(self, handler: Callable[[web.WebSocketResponse], Awaitable[None]]) -> None:
        self.handler = handler
        self.runner: web.AppRunner | None = None
        self.port: int | None = None
        self.received: list[dict] = []

    async def __aenter__(self) -> "FakeGateway":
        async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await self.handler(ws)
            return ws

        app = web.Application()
        app.router.add_get("/", _ws_handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host="127.0.0.1", port=0)
        await site.start()
        # Pull the port from the underlying server.
        self.port = next(iter(site._server.sockets)).getsockname()[1]  # type: ignore[union-attr]
        return self

    async def __aexit__(self, *_exc) -> None:
        if self.runner is not None:
            await self.runner.cleanup()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    ident.reset_cache()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield
    ident.reset_cache()
    cfg_mod.reset_config()


async def _send_json(ws: web.WebSocketResponse, obj: dict) -> None:
    await ws.send_str(json.dumps(obj))


async def _recv_json(ws: web.WebSocketResponse) -> dict:
    msg = await ws.receive(timeout=5)
    assert msg.type == WSMsgType.TEXT
    return json.loads(msg.data)


# --- Tests ---


@pytest.mark.asyncio
async def test_connect_handshake_then_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    received_connect: dict = {}

    async def handler(ws: web.WebSocketResponse) -> None:
        await _send_json(ws, {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n-1"}})
        msg = await _recv_json(ws)
        assert msg["method"] == "connect"
        received_connect.update(msg)
        await _send_json(
            ws,
            {"type": "res", "id": msg["id"], "ok": True, "payload": {"auth": {"scopes": ["operator.read"]}}},
        )

        # Wait for chat.send.
        chat_msg = await _recv_json(ws)
        assert chat_msg["method"] == "chat.send"
        assert chat_msg["params"]["sessionKey"] == "vauxr:dev1"
        assert chat_msg["params"]["message"] == "hello there"
        run_id = "run-xyz"
        await _send_json(ws, {"type": "res", "id": chat_msg["id"], "ok": True, "payload": {"runId": run_id}})

        # Stream a few deltas, then final.
        for piece in ["hi ", "there"]:
            await _send_json(
                ws,
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {"state": "delta", "runId": run_id, "message": {"content": [{"text": piece}]}},
                },
            )
        await _send_json(ws, {"type": "event", "event": "chat", "payload": {"state": "final", "runId": run_id}})
        # Wait for the client to disconnect so the final message has time
        # to reach the listener before we tear down.
        # Block until the client tears down so the server-side send pump
        # finishes flushing our last messages before the WS closes.
        while not ws.closed:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
            except asyncio.TimeoutError:
                break
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break

    async with FakeGateway(handler) as fake:
        monkeypatch.setenv("OPENCLAW_URL", f"http://127.0.0.1:{fake.port}/")
        monkeypatch.setenv("OPENCLAW_TOKEN", "operator-tok")
        cfg_mod.reset_config()

        client = OpenClawClient()
        try:
            await client.connect()
            deltas: list[str] = []
            await client.chat("vauxr:dev1", "hello there", deltas.append)
            assert deltas == ["hi ", "there"]
        finally:
            await client.close()

    # Sanity-check the connect payload looks like the Node version.
    params = received_connect["params"]
    assert params["minProtocol"] == 4
    assert params["maxProtocol"] == 4
    assert params["client"]["platform"] == "node"
    assert params["device"]["nonce"] == "n-1"
    assert isinstance(params["device"]["signature"], str)


@pytest.mark.asyncio
async def test_chat_error_state_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(ws: web.WebSocketResponse) -> None:
        await _send_json(ws, {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}})
        msg = await _recv_json(ws)
        await _send_json(ws, {"type": "res", "id": msg["id"], "ok": True, "payload": {"auth": {}}})

        chat_msg = await _recv_json(ws)
        run_id = "run-err"
        await _send_json(ws, {"type": "res", "id": chat_msg["id"], "ok": True, "payload": {"runId": run_id}})
        await _send_json(
            ws,
            {
                "type": "event",
                "event": "chat",
                "payload": {"state": "error", "runId": run_id, "errorMessage": "boom"},
            },
        )
        # Block until the client tears down so the server-side send pump
        # finishes flushing our last messages before the WS closes.
        while not ws.closed:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
            except asyncio.TimeoutError:
                break
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break

    async with FakeGateway(handler) as fake:
        monkeypatch.setenv("OPENCLAW_URL", f"http://127.0.0.1:{fake.port}/")
        cfg_mod.reset_config()

        client = OpenClawClient()
        try:
            await client.connect()
            with pytest.raises(RuntimeError, match="boom"):
                await client.chat("vauxr:dev1", "hi", lambda _t: None)
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_chat_send_response_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(ws: web.WebSocketResponse) -> None:
        await _send_json(ws, {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}})
        msg = await _recv_json(ws)
        await _send_json(ws, {"type": "res", "id": msg["id"], "ok": True, "payload": {"auth": {}}})

        chat_msg = await _recv_json(ws)
        await _send_json(
            ws,
            {
                "type": "res",
                "id": chat_msg["id"],
                "ok": False,
                "error": {"message": "rate limited"},
            },
        )
        # Block until the client tears down so the server-side send pump
        # finishes flushing our last messages before the WS closes.
        while not ws.closed:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=2.0)
            except asyncio.TimeoutError:
                break
            if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                break

    async with FakeGateway(handler) as fake:
        monkeypatch.setenv("OPENCLAW_URL", f"http://127.0.0.1:{fake.port}/")
        cfg_mod.reset_config()

        client = OpenClawClient()
        try:
            await client.connect()
            with pytest.raises(RuntimeError, match="rate limited"):
                await client.chat("vauxr:dev1", "hi", lambda _t: None)
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_pairing_required_resolves_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    """When OpenClaw returns PAIRING_REQUIRED, connect() should not raise."""

    async def handler(ws: web.WebSocketResponse) -> None:
        await _send_json(ws, {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}})
        msg = await _recv_json(ws)
        await _send_json(
            ws,
            {
                "type": "res",
                "id": msg["id"],
                "ok": False,
                "error": {"message": "PAIRING_REQUIRED — approve via /pair"},
            },
        )
        # Hold the connection open so the test can finish before close.
        await asyncio.sleep(0.5)

    async with FakeGateway(handler) as fake:
        monkeypatch.setenv("OPENCLAW_URL", f"http://127.0.0.1:{fake.port}/")
        cfg_mod.reset_config()

        client = OpenClawClient()
        try:
            await client.connect()  # should not raise
            assert client.connected is False  # pairing required, not yet connected
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_save_new_device_token_on_connect(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def handler(ws: web.WebSocketResponse) -> None:
        await _send_json(ws, {"type": "event", "event": "connect.challenge", "payload": {"nonce": "n"}})
        msg = await _recv_json(ws)
        await _send_json(
            ws,
            {
                "type": "res",
                "id": msg["id"],
                "ok": True,
                "payload": {"auth": {"deviceToken": "freshly-issued-dt"}},
            },
        )
        await asyncio.sleep(0.5)

    async with FakeGateway(handler) as fake:
        monkeypatch.setenv("OPENCLAW_URL", f"http://127.0.0.1:{fake.port}/")
        cfg_mod.reset_config()

        client = OpenClawClient()
        try:
            await client.connect()
            # The reader task processes the response asynchronously; give it a
            # moment to fire save_device_token.
            await asyncio.sleep(0.05)
            assert ident.get_device_token(str(tmp_path)) == "freshly-issued-dt"
        finally:
            await client.close()
