"""Phase 12: channel_server WS handler."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from vauxr import channel_registry as cr, config as cfg_mod
from vauxr.channel_server import ChannelServer


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    cr._reset_for_tests()
    cr.load()
    yield
    cr._reset_for_tests()
    cfg_mod.reset_config()


@pytest.fixture
async def setup() -> AsyncIterator[tuple[ChannelServer, TestClient]]:
    cs = ChannelServer()
    app = web.Application()

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await cs.handle_connection(ws)
        return ws

    app.router.add_get("/channel", ws_handler)
    server = TestServer(app)
    async with TestClient(server) as c:
        yield cs, c


async def _send(ws, obj) -> None:
    await ws.send_str(json.dumps(obj))


async def _recv(ws) -> dict:
    msg = await ws.receive(timeout=2)
    assert msg.type == WSMsgType.TEXT
    return json.loads(msg.data)


@pytest.mark.asyncio
async def test_auth_valid_token_sends_ready(setup) -> None:
    cs, client = setup
    channel, token = await cr.create("Test Channel", "openclaw")

    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.auth", "token": token})
        ready = await _recv(ws)
        assert ready["type"] == "channel.ready"
        assert ready["channelId"] == channel.id
        assert ready["name"] == "Test Channel"


@pytest.mark.asyncio
async def test_auth_invalid_token_errors_and_closes(setup) -> None:
    _cs, client = setup
    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.auth", "token": "bad-token"})
        err = await _recv(ws)
        assert err == {"type": "error", "code": "UNAUTHORIZED", "message": "Invalid channel token"}
        msg = await ws.receive(timeout=2)
        assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)


@pytest.mark.asyncio
async def test_auth_non_active_channel_no_transcript(setup) -> None:
    cs, client = setup
    channel, token = await cr.create("Non-Active", "openclaw")
    # Don't activate it.
    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.auth", "token": token})
        await _recv(ws)

        # No active channel — send_transcript should fail.
        assert cs.send_transcript("dev1", "hello") is False


@pytest.mark.asyncio
async def test_transcript_routes_to_active_only(setup) -> None:
    cs, client = setup
    ch_active, tok_active = await cr.create("Active", "openclaw")
    ch_idle, tok_idle = await cr.create("Idle", "openclaw")
    cr.activate(ch_active.id)

    async with client.ws_connect("/channel") as ws_active, client.ws_connect("/channel") as ws_idle:
        await _send(ws_active, {"type": "channel.auth", "token": tok_active})
        await _recv(ws_active)
        await _send(ws_idle, {"type": "channel.auth", "token": tok_idle})
        await _recv(ws_idle)

        assert cs.send_transcript("dev1", "What is the weather?") is True

        msg = await _recv(ws_active)
        assert msg["type"] == "channel.transcript"
        assert msg["deviceId"] == "dev1"
        assert msg["sessionKey"] == "vauxr:dev1"
        assert msg["text"] == "What is the weather?"

        # Idle channel must not receive anything within 0.2s.
        try:
            extra = await asyncio.wait_for(ws_idle.receive(), timeout=0.2)
            assert extra.type != WSMsgType.TEXT, f"unexpected message: {extra.data!r}"
        except asyncio.TimeoutError:
            pass


@pytest.mark.asyncio
async def test_response_delta_routed_to_listener(setup) -> None:
    cs, client = setup
    ch, token = await cr.create("Ch", "openclaw")
    cr.activate(ch.id)

    deltas: list[tuple[str, str]] = []
    ends: list[str] = []
    cs.add_response_listener(
        "dev1",
        {
            "on_delta": lambda rid, t: deltas.append((rid, t)),
            "on_end": lambda rid: ends.append(rid),
            "on_error": lambda rid, m: None,
        },
    )

    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.auth", "token": token})
        await _recv(ws)
        await _send(ws, {"type": "channel.response.delta", "deviceId": "dev1", "runId": "r1", "text": "Hi "})
        await _send(ws, {"type": "channel.response.delta", "deviceId": "dev1", "runId": "r1", "text": "there"})
        await _send(ws, {"type": "channel.response.end", "deviceId": "dev1", "runId": "r1"})
        # Give the server a moment to process the messages.
        await asyncio.sleep(0.05)

    assert deltas == [("r1", "Hi "), ("r1", "there")]
    assert ends == ["r1"]


@pytest.mark.asyncio
async def test_response_error_routed(setup) -> None:
    cs, client = setup
    ch, token = await cr.create("Ch", "openclaw")
    cr.activate(ch.id)

    errors: list[tuple[str, str]] = []
    cs.add_response_listener(
        "dev1",
        {
            "on_delta": lambda *_: None,
            "on_end": lambda *_: None,
            "on_error": lambda rid, m: errors.append((rid, m)),
        },
    )

    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.auth", "token": token})
        await _recv(ws)
        await _send(
            ws,
            {
                "type": "channel.response.error",
                "deviceId": "dev1",
                "runId": "r1",
                "message": "Agent error",
            },
        )
        await asyncio.sleep(0.05)

    assert errors == [("r1", "Agent error")]


@pytest.mark.asyncio
async def test_connection_drop_then_send_transcript_returns_false(setup) -> None:
    cs, client = setup
    ch, token = await cr.create("Ch", "openclaw")
    cr.activate(ch.id)

    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.auth", "token": token})
        await _recv(ws)
        assert cs.send_transcript("dev1", "hi") is True

    # After disconnect, the server-side cleanup runs. Give it a tick.
    await asyncio.sleep(0.05)
    assert cs.send_transcript("dev1", "hi again") is False


@pytest.mark.asyncio
async def test_unauth_message_returns_unauthorized(setup) -> None:
    _cs, client = setup
    async with client.ws_connect("/channel") as ws:
        await _send(ws, {"type": "channel.transcript", "deviceId": "x", "text": "y"})
        err = await _recv(ws)
        assert err["code"] == "UNAUTHORIZED"
