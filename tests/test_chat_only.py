"""Accepted chat-only policy through real owner APIs and authenticated plugin WS."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests import test_agent_voice
from tests.auth_helpers import TRANSPORT_HEADERS, owner_headers, seed
from vauxr import chat, companion
from vauxr.agents import registry as agents
from vauxr.auth.policy import Role
from vauxr.auth.service import get_store
from vauxr.devices import registry as devices
from vauxr.server import APP_STATE, make_app
from vauxr.speech.store import SpeechStore, VoiceConflict


@pytest.fixture
async def portal(tmp_path, monkeypatch):
    store, first, second = await test_agent_voice.voice.__wrapped__(tmp_path, monkeypatch)
    monkeypatch.setenv("REALTIME_ENABLED", "1")
    monkeypatch.setenv("REALTIME_HOST", "127.0.0.1")
    from vauxr.config import reset_config
    reset_config()
    import vauxr.web.companion as http
    monkeypatch.setattr(http, "speech_store", lambda: store)
    for identity in ("B1", "B2", "physical"):
        seed(identity + "-secret", Role.DEVICE, identity)
    seed("agent-secret", Role.INTEGRATION, first.id)
    seed("other-agent-secret", Role.INTEGRATION, second.id)
    get_store().lifecycle = {"version": 1, "operations": {}, "blocked": [], "bindings": {
        identity: {"kind": "physical" if identity == "physical" else "browser", "public_key": "ab" * 32}
        for identity in ("B1", "B2", "physical")}, "recovery": {}}
    get_store().replace(get_store().records)
    devices.reset()
    app = make_app()
    async with TestClient(TestServer(app)) as client:
        headers = {**owner_headers(client), "X-Vauxr-Browser": "B1-secret"}
        yield client, headers, store, first, second
    chat._active.clear()
    chat._seen.clear()
    companion._views.clear()
    devices.reset()


async def policy(portal):
    client, headers, _, first, _ = portal
    result = await client.patch(f"/api/agents/{first.id}/speech", headers=headers,
                                json={"enabled_modes": [], "default_mode": None})
    assert result.status == 200, await result.text()
    return await result.json()


async def test_empty_modes_api_persistence_and_explicit_default_no_fallback(portal):
    client, headers, store, first, second = portal
    data = await policy(portal)
    assert data["policy"]["enabled_modes"] == []
    assert data["policy"]["default_mode"] is None
    assert data["policy"]["available_modes"] == []
    assert data["voice"]["mode"] is None
    assert data["effective"] is None and data["error"] is None
    assert not data["realtime"]["configured"]
    result = await client.post("/api/companion/select", headers=headers,
                               json={"browser_id": "B1", "agent_id": first.id})
    assert result.status == 200
    data = await (await client.get("/api/devices/B1/speech", headers=headers)).json()
    assert data["policy"]["chat_only"] and data["policy"]["conflict"] is None
    assert data["voice"]["mode"] is None
    assert {row["readiness"] for row in data["backends"]} == {"disabled (chat-only)"}
    restored = SpeechStore(store.path.parent, ())
    assert restored.agents[first.id] == {"enabled_modes": [], "default_mode": None, "selection": {}}
    assert restored.assignments["B1"] == first.id
    assert restored.voice_settings("B1")["mode"] is None
    assert restored.available_modes("B1") == []
    with pytest.raises(VoiceConflict, match="Chat-only"):
        restored.resolve("B1")
    before = store.path.read_bytes()
    result = await client.patch("/api/devices/B1/speech", headers=headers, json={"mode": "standard"})
    assert result.status == 409
    assert store.path.read_bytes() == before
    # Empty policy patch without a default canonically persists null too.
    store.update({"enabled_modes": []}, agent_id=second.id)
    assert SpeechStore(store.path.parent, ()).agents[second.id]["default_mode"] is None
    with pytest.raises(ValueError, match="Default mode"):
        store.update({"enabled_modes": [], "default_mode": "standard"}, agent_id=second.id)
    with pytest.raises(ValueError, match="Default mode"):
        store.update({"enabled_modes": ["standard"]}, agent_id=second.id)
    store.update({"enabled_modes": ["standard"], "default_mode": "standard"}, agent_id=second.id)
    assert store.agent_defaults(second.id)["mode"] == "standard"


async def test_physical_conflict_and_browser_voice_rejected_before_media(portal, monkeypatch):
    client, headers, store, first, second = portal
    await policy(portal)
    # A new physical device following the fallback gets an explicit no-voice policy.
    ws = await client.ws_connect("/ws", headers=TRANSPORT_HEADERS)
    await ws.send_json({"type": "hello", "device_id": "physical", "token": "physical-secret", "caps": ["ws", "webrtc"]})
    hello = await ws.receive_json()
    assert hello["pipeline_mode"] is None and not hello["voice_enabled"]
    assert not hello["realtime"]["enabled"] and hello["chat_only"]
    assert (await ws.receive_json())["code"] == "VOICE_CONFLICT"
    before = store.path.read_bytes()
    result = await client.patch("/api/devices/physical/speech", headers=headers, json={"agent_id": first.id})
    assert result.status == 409
    assert store.path.read_bytes() == before
    from vauxr import buttons, pipeline, server
    resolve = AsyncMock(side_effect=AssertionError("No voice attempt"))
    monkeypatch.setattr(server, "resolve", resolve)
    monkeypatch.setattr(pipeline, "transcribe", resolve)
    monkeypatch.setattr(buttons, "synthesize", resolve)
    monkeypatch.setattr(buttons, "run_text_turn", resolve)
    for identity in ("physical", "B1"):
        socket = ws if identity == "physical" else await client.ws_connect("/ws", headers=TRANSPORT_HEADERS)
        if identity == "B1":
            await socket.send_json({"type": "hello", "device_id": identity, "token": identity + "-secret", "caps": ["ws", "webrtc"]})
            assert (await socket.receive_json())["chat_only"]
        device_list = await (await client.get("/api/devices", headers=headers)).json()
        visible = next(device for device in device_list if device["id"] == identity)
        assert visible["chat_only"]
        assert visible["config"]["pipeline_mode"] is None
        assert visible["config"]["voice_mode"] is None
        for operation in ({"type": "voice.start"}, {"type": "realtime.start"}, {"type": "realtime.start", "mode": "live"}):
            await socket.send_json(operation)
            rejection = await socket.receive_json()
            assert rejection["code"] == "VOICE_CONFLICT" and "Chat-only" in rejection["message"]
        response = await client.post("/api/offer", headers=TRANSPORT_HEADERS,
            json={"device_id": identity, "token": identity + "-secret", "sdp": "invalid", "type": "offer"})
        assert response.status == 409
        assert "Chat-only" in (await response.json())["error"]
        response = await client.post(f"/api/devices/{identity}/announce", headers=headers, json={"text": "Do not speak"})
        assert response.status == 409
        response = await client.patch(f"/api/devices/{identity}", headers=headers,
            json={"button_actions": {"double_press": {"kind": "prompt", "text": "No voice"},
                                     "triple_press": {"kind": "announce", "text": "No voice"}}})
        assert response.status == 200
        for gesture in ("double_press", "triple_press"):
            await socket.send_json({"type": "device.button", "gesture": gesture})
            assert (await socket.receive_json())["code"] == "VOICE_CONFLICT"
        await socket.close()
    resolve.assert_not_called()
    # Moving physical capability to a voice-enabled agent is an explicit resolution.
    store.update({"agent_id": second.id}, "physical")
    assert agents.get_for_device("physical").id == second.id


async def test_text_uses_actual_dispatcher_context_correlations_and_no_voice(portal, monkeypatch):
    client, headers, _store, first, second = portal
    await policy(portal)
    from vauxr import pipeline, server
    no_voice = AsyncMock(side_effect=AssertionError("No STT, TTS or voice pipeline"))
    monkeypatch.setattr(pipeline, "synthesize", no_voice)
    monkeypatch.setattr(pipeline, "transcribe", no_voice)
    monkeypatch.setattr(server, "run_voice_turn", no_voice)
    agent = await client.ws_connect("/agent", headers=TRANSPORT_HEADERS)
    await agent.send_json({"type": "agent.auth", "token": "agent-secret"})
    assert (await agent.receive_json())["type"] == "agent.ready"
    ctx = {"browser_id": "B1", "route": "agents", "resource_type": "agent",
           "resource_id": first.id, "setting_key": "name", "current_value": first.name}
    assert (await client.post("/api/companion/context", headers=headers, json=ctx)).status == 200
    # A detached earlier transcript must not be mistaken for this HTTP request.
    assert client.app[APP_STATE].agent_server.send_transcript("B1", "Earlier detached transcript")
    assert (await agent.receive_json())["deviceId"] == "B1"
    body = {"browser_id": "B1", "request_id": "aa" * 16, "text": "Explain this name"}
    pending = asyncio.create_task(client.post("/api/companion/chat", headers=headers, json=body))
    frame = await agent.receive_json()
    assert frame["type"] == "agent.transcript" and frame["deviceId"] == "B1"
    assert "Explain this name" in frame["text"] and "Untrusted browser UI data" in frame["text"]
    assert "B1-secret" not in frame["text"] and "agent-secret" not in frame["text"]
    # Request locking reuses #100's active change protection.
    assert (await client.post("/api/companion/select", headers=headers,
            json={"browser_id": "B1", "agent_id": second.id})).status == 409
    await agent.send_json({"type": "agent.response.delta", "deviceId": "B2", "runId": "wrong", "text": "private"})
    await agent.send_json({"type": "agent.response.delta", "deviceId": "B1", "runId": "earlier", "text": "stale earlier text"})
    await agent.send_json({"type": "agent.response.end", "deviceId": "B1", "runId": "earlier"})
    # Neither another device nor an earlier unbound transcript completes this request.
    await asyncio.sleep(0)
    assert not pending.done()
    await agent.send_json({"type": "agent.response.delta", "deviceId": "B1", "runId": "run", "text": "Text only response"})
    await agent.send_json({"type": "agent.response.end", "deviceId": "B1", "runId": "run"})
    response = await pending
    assert response.status == 200
    assert await response.json() == {"request_id": body["request_id"], "text": "Text only response"}
    assert not client.app[APP_STATE].agent_server.get_response_listener("B1")
    assert not chat.is_active("B1")
    assert (await client.post("/api/companion/chat", headers=headers, json=body)).status == 409
    no_voice.assert_not_called()
    await agent.close()


@pytest.mark.parametrize("patch,body_id,status", [
    ({"Cookie": ""}, "B1", 401), ({"X-CSRF-Token": "wrong"}, "B1", 403),
    ({"X-Vauxr-Browser": "physical-secret"}, "physical", 403),
    ({"X-Vauxr-Browser": "agent-secret"}, "B1", 403),
    ({"X-Vauxr-Browser": "B2-secret"}, "B1", 409),
    ({"Origin": "https://foreign.example"}, "B1", 403),
])
async def test_text_owner_browser_identity_and_csrf_negatives(portal, patch, body_id, status):
    client, headers, *_ = portal
    response = await client.post("/api/companion/chat", headers={**headers, **patch},
        json={"browser_id": body_id, "request_id": "ab" * 16, "text": "denied"})
    assert response.status == status
    assert "secret" not in await response.text()


async def test_text_credential_retirement_and_detached_reply_do_not_reach_replacement(portal):
    client, headers, _store, _first, _second = portal
    await policy(portal)
    agent = await client.ws_connect("/agent", headers=TRANSPORT_HEADERS)
    await agent.send_json({"type": "agent.auth", "token": "agent-secret"})
    assert (await agent.receive_json())["type"] == "agent.ready"
    pending = asyncio.create_task(client.post("/api/companion/chat", headers=headers,
        json={"browser_id": "B1", "request_id": "01" * 16, "text": "First request"}))
    assert (await agent.receive_json())["deviceId"] == "B1"
    # Revoke exact browser authority, preserving the shared agent connection.
    from vauxr.auth.connections import disconnect_stale
    get_store().replace(tuple(record for record in get_store().records if record.subject != "B1"))
    await disconnect_stale(get_store())
    response = await pending
    assert response.status == 409
    assert not chat.is_active("B1")
    assert "retired" in (await response.json())["error"]
    # A later, newly authenticated B1 request cannot inherit the old run's output.
    seed("replacement-secret", Role.DEVICE, "B1")
    new_headers = {**headers, "X-Vauxr-Browser": "replacement-secret"}
    pending = asyncio.create_task(client.post("/api/companion/chat", headers=new_headers,
        json={"browser_id": "B1", "request_id": "02" * 16, "text": "Replacement request"}))
    assert (await agent.receive_json())["deviceId"] == "B1"
    await agent.send_json({"type": "agent.response.delta", "deviceId": "B1", "runId": "old", "text": "stale private response"})
    await agent.send_json({"type": "agent.response.end", "deviceId": "B1", "runId": "old"})
    await agent.send_json({"type": "agent.response.delta", "deviceId": "B1", "runId": "new", "text": "Replacement response"})
    await agent.send_json({"type": "agent.response.end", "deviceId": "B1", "runId": "new"})
    response = await pending
    assert response.status == 200
    assert (await response.json())["text"] == "Replacement response"
    await agent.close()
