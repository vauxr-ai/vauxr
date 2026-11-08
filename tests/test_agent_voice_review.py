"""Independent-review regressions at actual mutation and dispatch boundaries."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from tests import test_agent_voice
from tests.auth_helpers import owner_headers, seed
from vauxr.agents import registry as agents
from vauxr.agents.server import AgentServer
from vauxr.auth.policy import Role
from vauxr.config import get_config, reset_config
from vauxr.speech.store import SpeechStore


@pytest.fixture
async def review_voice(tmp_path, monkeypatch):
    return await test_agent_voice.voice.__wrapped__(tmp_path, monkeypatch)


async def test_fallback_validates_merged_live_voice_before_either_store_changes(review_voice):
    store, first, second = review_voice
    from vauxr.web.server import make_http_app

    store.update({"enabled_modes": ["realtime"], "default_mode": "realtime"}, agent_id=first.id)
    store.update(
        {
            "enabled_modes": ["realtime"],
            "default_mode": "realtime",
            "realtime_backend": "other-live",
            "realtime_voice": "sage",
        },
        agent_id=second.id,
    )
    store.record_capabilities("D1", ["webrtc"])
    store.update({"realtime_voice": "ash"}, "D1")
    routing = Path(get_config().data_dir) / "agents.json"
    before = routing.read_bytes(), store.path.read_bytes()
    async with TestClient(TestServer(make_http_app())) as client:
        headers = owner_headers(client)
        response = await client.post(f"/api/agents/{second.id}/activate", headers=headers)
        assert response.status == 409
        assert "D1" in (await response.json())["error"]
        assert (routing.read_bytes(), store.path.read_bytes()) == before
        assert agents.get_active().id == first.id
        assert SpeechStore(store.path.parent, ()).voice_settings("D1")["realtime_voice"] == "ash"
        store.update({"realtime_voice": None}, "D1")
        response = await client.post(f"/api/agents/{second.id}/activate", headers=headers)
        assert response.status == 200
    restored = SpeechStore(store.path.parent, ())
    assert restored.voice_settings("D1")["realtime_voice"] == "sage"
    # Removal/revocation must win over routing, without making speech.json unreadable
    # or choosing a different agent. The orphaned override is a resolvable conflict.
    restored.update({"realtime_voice": "sage"}, "D1")
    assert agents.remove(second.id)
    restored = SpeechStore(store.path.parent, ())
    assert agents.get_for_device("D1") is None
    assert "unavailable" in restored.view("D1")["policy"]["conflict"]


async def test_every_enabled_mode_requires_its_provider_and_voice(review_voice, tmp_path):
    store, first, _second = review_voice
    no_live = SpeechStore(
        tmp_path / "no-live", tuple(b for b in store.backends.values() if b.kind != "realtime")
    )
    no_live.update({"enabled_modes": ["standard"], "default_mode": "standard"}, agent_id=first.id)
    before = no_live.path.read_bytes()
    with pytest.raises(ValueError, match="registered Live provider and matching voice"):
        no_live.update({"enabled_modes": ["standard", "realtime"]}, agent_id=first.id)
    assert no_live.path.read_bytes() == before
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="registered Live provider and matching voice"):
        store.update(
            {
                "enabled_modes": ["standard", "realtime"],
                "default_mode": "standard",
                "realtime_backend": "other-live",
                "realtime_voice": "ash",
            },
            agent_id=first.id,
        )
    assert store.path.read_bytes() == before
    # Provider + mode + default can be supplied in one transaction.
    no_live_with_catalog = json.loads(no_live.path.read_text())
    from dataclasses import asdict

    no_live_with_catalog["providers"].append(asdict(store.backends["other-live"]))
    no_live.path.write_text(json.dumps(no_live_with_catalog))
    no_live = SpeechStore(no_live.path.parent, ())
    no_live.update(
        {
            "enabled_modes": ["realtime"],
            "default_mode": "realtime",
            "realtime_backend": "other-live",
            "realtime_voice": "sage",
        },
        agent_id=first.id,
    )
    assert no_live.voice_settings(agent_id=first.id)["realtime_voice"] == "sage"


async def _recv(ws):
    frame = await ws.receive(timeout=2)
    assert frame.type == WSMsgType.TEXT
    return json.loads(frame.data)


@pytest.mark.parametrize("first_reply_after_switch", [False, True])
async def test_stale_standard_reply_drops_only_D1_and_preserves_shared_D2(
    review_voice, first_reply_after_switch
):
    store, first, second = review_voice
    seed("A-token", Role.INTEGRATION, first.id)
    server = AgentServer()
    app = web.Application()

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await server.handle_connection(ws)
        return ws

    app.router.add_get("/agent", socket)
    heard = []
    listener = lambda identity: {
        "on_delta": lambda run, text: heard.append((identity, text)),
        "on_end": lambda run: None,
        "on_error": lambda run, error: None,
    }
    async with TestClient(TestServer(app)) as client, client.ws_connect("/agent") as ws:
        await ws.send_json({"type": "agent.auth", "token": "A-token"})
        assert (await _recv(ws))["type"] == "agent.ready"
        server.add_response_listener("D1", listener("D1"))
        assert server.send_transcript("D1", "old request")
        assert (await _recv(ws))["deviceId"] == "D1"
        if not first_reply_after_switch:
            await ws.send_json(
                {"type": "agent.response.delta", "deviceId": "D1", "runId": "old", "text": "admitted"}
            )
            await asyncio.sleep(0.02)
        heard.clear()
        server.remove_response_listener("D1")  # Local stop does not cancel the external operation.
        store.update({"agent_id": second.id}, "D1")
        server.add_response_listener("D1", listener("replacement"))
        server.add_response_listener("D2", listener("D2"))
        assert server.send_transcript("D2", "unrelated request")
        assert (await _recv(ws))["deviceId"] == "D2"
        for kind in ("agent.response.delta", "agent.response.end"):
            await ws.send_json({"type": kind, "deviceId": "D1", "runId": "old", "text": "stale"})
        await ws.send_json(
            {"type": "agent.response.delta", "deviceId": "D2", "runId": "new", "text": "survives"}
        )
        await asyncio.sleep(0.02)
        assert heard == [("D2", "survives")]
        assert not ws.closed and first.id in server._connections
        # A made-up stream on the now-unassigned device has no admission proof.
        await ws.send_json(
            {"type": "agent.response.delta", "deviceId": "D1", "runId": "unknown", "text": "attack"}
        )
        assert (await _recv(ws))["code"] == "FORBIDDEN"
        await ws.send_json(
            {"type": "agent.response.delta", "deviceId": "D2", "runId": "new", "text": "still alive"}
        )
        await asyncio.sleep(0.02)
        assert not ws.closed
        assert heard == [("D2", "survives"), ("D2", "still alive")]


@pytest.mark.parametrize("cancel_D1", [False, True])
async def test_stale_realtime_result_does_not_cancel_D2_or_close_A(review_voice, cancel_D1):
    store, first, second = review_voice
    from vauxr.auth.service import authenticate

    seed("A-token", Role.INTEGRATION, first.id)
    server = AgentServer()
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock())
    conn = SimpleNamespace(ws=ws, principal=authenticate("A-token"), agent=first)
    server._connections[first.id] = conn
    old = asyncio.create_task(server.realtime_request(first.id, "D1", "session1", "consult"))
    await asyncio.sleep(0)
    frame1 = json.loads(ws.send_str.call_args.args[0])
    other = asyncio.create_task(server.realtime_request(first.id, "D2", "session2", "consult"))
    await asyncio.sleep(0)
    frame2 = json.loads(ws.send_str.call_args.args[0])
    if cancel_D1:
        old.cancel()
        with pytest.raises(asyncio.CancelledError):
            await old
    store.update({"agent_id": second.id}, "D1")
    await server._handle_authenticated_message(
        conn,
        {
            "type": "agent.realtime.result",
            "deviceId": "D1",
            "requestId": frame1["requestId"],
            "result": {"text": "stale"},
        },
    )
    if not cancel_D1:
        with pytest.raises(RuntimeError, match="stale realtime result"):
            await old
    assert not other.done()
    ws.close.assert_not_awaited()
    await server._handle_authenticated_message(
        conn,
        {
            "type": "agent.realtime.result",
            "deviceId": "D2",
            "requestId": frame2["requestId"],
            "result": {"text": "kept"},
        },
    )
    assert await other == {"text": "kept"}
    ws.close.assert_not_awaited()
    # Admission history never bypasses current credentials or bound identity.
    seed("B-token", Role.INTEGRATION, second.id)
    for principal, code in ((None, "UNAUTHORIZED"), (authenticate("B-token"), "FORBIDDEN")):
        wrong = SimpleNamespace(
            ws=SimpleNamespace(send_str=AsyncMock(), close=AsyncMock(), closed=False),
            principal=principal,
            agent=first,
        )
        await server._handle_authenticated_message(
            wrong,
            {
                "type": "agent.realtime.result",
                "deviceId": "D1",
                "requestId": frame1["requestId"],
                "result": {"text": "attack"},
            },
        )
        wrong.ws.close.assert_awaited_once()
        assert json.loads(wrong.ws.send_str.call_args.args[0])["code"] == code
    ws.close.assert_not_awaited()


@pytest.mark.parametrize("assign_before_startup", [False, True])
async def test_direct_client_lifetime_and_actual_standard_dispatch_with_global_integration(
    review_voice, monkeypatch, assign_before_startup
):
    store, first, _second = review_voice
    from vauxr import pipeline, server
    from vauxr.devices import registry

    registry.reset()
    monkeypatch.setenv("OPENCLAW_URL", "ws://127.0.0.1:1")
    reset_config()
    agents.load()
    assert agents.get_active().id == first.id
    if assign_before_startup:
        store.update({"agent_id": "openclaw-direct"}, "D1")
    client = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), chat=AsyncMock())

    async def chat(session, text, on_delta):
        assert session == "vauxr:D1" and text == "hello"
        on_delta("Direct reply")

    client.chat.side_effect = chat
    monkeypatch.setattr(server, "OpenClawClient", lambda: client)
    monkeypatch.setattr(server, "get_speech_store", lambda: store)
    app = server.make_app()
    try:
        await server._startup(app)
        client.connect.assert_awaited_once()
        if not assign_before_startup:
            store.update({"agent_id": "openclaw-direct"}, "D1")
        ws = SimpleNamespace(closed=False, send_str=AsyncMock(), send_bytes=AsyncMock())

        async def synthesize(text, **kwargs):
            assert text == "Direct reply"
            yield b"\x00\x00"

        monkeypatch.setattr(pipeline, "synthesize", synthesize)
        await pipeline.run_text_turn(
            "D1",
            "hello",
            ws,
            app[server.APP_STATE].openclaw_client,
            app[server.APP_STATE].agent_server,
            asyncio.Event(),
        )
        client.chat.assert_awaited_once()
        frames = [json.loads(call.args[0]) for call in ws.send_str.call_args_list]
        assert any(frame["type"] == "audio.end" for frame in frames)
        assert not any(frame.get("code") == "NO_AGENT" for frame in frames)
        assert ws.send_bytes.await_count == 1
        assert agents.get_active().id == first.id
    finally:
        await server._cleanup(app)
        client.close.assert_awaited_once()
        registry.reset()
