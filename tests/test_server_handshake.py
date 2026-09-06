"""Phase 4: device WS server handshake."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestServer, TestClient

import config as cfg_mod
from server import make_app


@pytest.fixture(autouse=True)
def _device_token(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "ws-test-token")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield
    cfg_mod.reset_config()


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    app = make_app()
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def _recv_json(ws) -> dict:
    msg = await ws.receive(timeout=2)
    assert msg.type == WSMsgType.TEXT, f"got {msg.type}: {msg.data!r}"
    return json.loads(msg.data)


async def test_voice_start_valid_token_emits_ready(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "voice.start", "device_id": "dev1", "token": "ws-test-token"})
        ready = await _recv_json(ws)
        assert ready == {"type": "ready"}


async def test_voice_start_invalid_token_emits_error_and_closes(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "voice.start", "device_id": "dev1", "token": "wrong-token-xxxxx"})
        err = await _recv_json(ws)
        assert err["type"] == "error"
        assert err["code"] == "UNAUTHORIZED"
        # Server should have closed the connection.
        msg = await ws.receive(timeout=2)
        assert msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)


async def test_voice_start_missing_token(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "voice.start", "device_id": "dev1"})
        err = await _recv_json(ws)
        assert err == {
            "type": "error",
            "code": "INVALID_MESSAGE",
            "message": "Missing device_id or token",
        }


async def test_unknown_message_type_returns_error(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "not.a.real.type"})
        err = await _recv_json(ws)
        assert err["type"] == "error"
        assert err["code"] == "UNKNOWN_MESSAGE"


async def test_invalid_json_returns_error(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_str("not json {{")
        err = await _recv_json(ws)
        assert err["type"] == "error"
        assert err["code"] == "INVALID_MESSAGE"


async def test_voice_end_without_voice_start_is_error(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "voice.end"})
        err = await _recv_json(ws)
        assert err == {
            "type": "error",
            "code": "INVALID_STATE",
            "message": "Not in listening state",
        }


async def test_voice_start_then_end_transitions_state(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "voice.start", "device_id": "dev1", "token": "ws-test-token"})
        await _recv_json(ws)  # ready
        # Phase 4 — voice.end is accepted but no pipeline output yet
        await ws.send_json({"type": "voice.end"})
        # No error expected; nothing emitted either.
        # Close cleanly.
        await ws.close()


async def test_output_sample_rate_accepted(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "voice.start",
                "device_id": "dev1",
                "token": "ws-test-token",
                "output_sample_rate": 24000,
            }
        )
        ready = await _recv_json(ws)
        assert ready == {"type": "ready"}


async def test_device_button_is_not_unknown_message(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "dev1",
                "token": "ws-test-token",
                "caps": ["ws"],
            }
        )
        hello = await _recv_json(ws)
        assert hello["type"] == "hello"
        await ws.send_json(
            {"type": "device.button", "button": "action", "gesture": "double_press"}
        )
        await ws.send_json({"type": "not.a.real.type"})
        err = await _recv_json(ws)
        assert err["code"] == "UNKNOWN_MESSAGE"
        assert "not.a.real.type" in err["message"]


async def test_device_button_without_hello_is_ignored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    async def fake_handle(**kwargs):
        called.append(kwargs.get("device_id") or "")

    monkeypatch.setattr("button_dispatch.handle_device_button", fake_handle)
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "device.button",
                "device_id": "victim",
                "button": "action",
                "gesture": "double_press",
            }
        )
        await ws.send_json({"type": "not.a.real.type"})
        err = await _recv_json(ws)
        assert err["code"] == "UNKNOWN_MESSAGE"
    assert called == []


async def test_device_button_ignores_spoofed_device_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []

    async def fake_handle(**kwargs):
        called.append(kwargs.get("device_id") or "")

    monkeypatch.setattr("button_dispatch.handle_device_button", fake_handle)
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "dev1",
                "token": "ws-test-token",
                "caps": ["ws"],
            }
        )
        hello = await _recv_json(ws)
        assert hello["type"] == "hello"
        await ws.send_json(
            {
                "type": "device.button",
                "device_id": "victim",
                "button": "action",
                "gesture": "double_press",
            }
        )
        await ws.send_json({"type": "not.a.real.type"})
        err = await _recv_json(ws)
        assert err["code"] == "UNKNOWN_MESSAGE"
    assert called == ["dev1"]
