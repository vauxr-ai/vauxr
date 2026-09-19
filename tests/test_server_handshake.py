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
    from tests.auth_helpers import seed
    from auth_policy import Role
    seed("ws-test-token", Role.DEVICE, "dev1")
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
            "code": "UNAUTHORIZED",
            "message": "Access denied",
        }


async def test_unknown_message_type_returns_error(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "not.a.real.type"})
        err = await _recv_json(ws)
        assert err["type"] == "error"
        assert err["code"] in {"UNAUTHORIZED", "FORBIDDEN"}


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
            "code": "UNAUTHORIZED",
            "message": "Access denied",
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
        assert err["code"] in {"UNAUTHORIZED", "FORBIDDEN"}
        assert err["message"] == "Access denied"


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
        assert err["code"] in {"UNAUTHORIZED", "FORBIDDEN"}
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
        assert err["code"] in {"UNAUTHORIZED", "FORBIDDEN"}
    assert called == []


@pytest.mark.parametrize("replacement_ended", [False, True])
@pytest.mark.parametrize("old_fails", [False, True])
async def test_aborted_turn_finally_cannot_clear_replacement(
    monkeypatch: pytest.MonkeyPatch, replacement_ended: bool, old_fails: bool,
) -> None:
    import asyncio
    from unittest.mock import AsyncMock
    import server
    import device_registry as registry

    release = asyncio.Event()
    started = asyncio.Event()
    finished = asyncio.Event()
    calls = 0

    async def run(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            finished.set()
            if old_fails:
                raise RuntimeError("Late failure from aborted pipeline")
        else:
            await asyncio.Event().wait()

    tasks: list[asyncio.Task[None]] = []
    create_task = asyncio.create_task
    monkeypatch.setattr(
        server.asyncio, "create_task", lambda coro: tasks.append(create_task(coro)) or tasks[-1],
    )
    monkeypatch.setattr(server, "run_voice_turn", run)
    monkeypatch.setattr(server, "resolve", lambda device_id: None)
    ws = AsyncMock()
    ws.closed = False
    ctx = server.ConnectionCtx(device_id="dev1")
    state = server.AppState()
    registry.register("dev1", ws, name="Stored browser name")
    try:
        await server._voice_start(state, ws, ctx, {})
        await server._voice_end(state, ws, ctx)
        await started.wait()
        await server._voice_start(state, ws, ctx, {})
        assert registry.get("dev1").name == "Stored browser name"
        if replacement_ended:
            await server._voice_end(state, ws, ctx)
        replacement = registry.get("dev1").abort_event
        release.set()
        await finished.wait()
        await tasks[0]
        expected = server.ConnectionState.PROCESSING if replacement_ended else server.ConnectionState.LISTENING
        assert ctx.state == expected
        assert registry.get("dev1").state == ("processing" if replacement_ended else "listening")
        assert registry.get("dev1").abort_event is replacement
        messages = [json.loads(call.args[0]) for call in ws.send_str.call_args_list]
        assert messages == [{"type": "ready"}, {"type": "ready"}]
        if replacement_ended:
            assert replacement is not None and not replacement.is_set()
            server._voice_abort(ctx)
            assert replacement.is_set()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        registry.unregister("dev1")
