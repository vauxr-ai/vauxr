"""Agent voice inheritance through the persisted store and authenticated runtime."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import create_agent, owner_headers, seed
from vauxr.agents import registry as agents
from vauxr.auth.policy import Role
from vauxr.speech import store as speech
from vauxr.speech.models import Backend
from vauxr.speech.store import SpeechStore, VoiceConflict


@pytest.fixture
async def voice(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    from vauxr.config import reset_config

    reset_config()
    first, _ = await create_agent("Household")
    second, _ = await create_agent("Workshop")
    agents.activate(first.id)
    store = SpeechStore(
        tmp_path,
        (
            Backend("stt", "stt", "whisper", "small", "127.0.0.1", 1),
            Backend("tts", "tts", "piper", "piper", "127.0.0.1", 1, ("amy", "sam")),
            Backend(
                "live", "realtime", "openai-live", "gpt-realtime", "api.openai.com", 443, ("ash", "coral")
            ),
            Backend("other-live", "realtime", "openai-live", "other", "api.openai.com", 443, ("sage",)),
        ),
    )
    # Import the HTTP boundary before patching the store function so its
    # original module alias cannot retain the first test's private store.
    import vauxr.web.speech as http

    monkeypatch.setattr(speech, "get_store", lambda: store)
    monkeypatch.setattr(http, "get_store", lambda: store)
    monkeypatch.setattr(http, "readiness", AsyncMock(return_value="unavailable"))
    return store, first, second


async def test_version_one_migration_preserves_all_effective_values(voice):
    store, _first, _ = voice
    # Before any explicit policy, retain global and per-device legacy behavior.
    store.update({"mode": "realtime", "realtime_voice": "coral"})
    store.update({"mode": "standard", "voices": {"tts": "sam"}}, "fixed")
    legacy = json.loads(store.path.read_text())
    legacy = {key: legacy[key] for key in ("providers", "defaults", "devices")}
    store.path.write_text(json.dumps({"version": 1, **legacy}))
    restored = SpeechStore(store.path.parent, ())
    assert restored.voice_settings("unseen")["mode"] == "realtime"
    assert restored.voice_settings("fixed")["mode"] == "standard"
    assert restored.resolve("fixed").voice_id == "sam"
    before = restored.voice_settings("unseen")
    restored.record_capabilities("fixed", ["ws"])
    assert restored.voice_settings("unseen") == before
    assert json.loads(restored.path.read_text())["version"] == 2
    assert restored.agents == {}  # Migration does not invent a chat/voice policy.


async def test_assignment_modes_distinct_providers_reset_and_restart(voice):
    store, first, second = voice
    store.update(
        {
            "enabled_modes": ["standard", "realtime"],
            "default_mode": "standard",
            "voices": {"tts": "sam"},
            "realtime_backend": "other-live",
            "realtime_voice": "sage",
        },
        agent_id=second.id,
    )
    store.record_capabilities("speaker", ["ws", "webrtc", "invented"])
    store.update({"agent_id": second.id}, "speaker")
    assert agents.get_for_device("speaker").id == second.id
    assert agents.get_for_device("other").id == first.id
    assert store.resolve("speaker").voice_id == "sam"
    store.update(
        {"mode": "realtime", "realtime_backend": "live", "realtime_voice": "coral", "voices": {"tts": "amy"}},
        "speaker",
    )
    assert store.voice_settings("speaker")["realtime_voice"] == "coral"
    store.update({"reset": True}, "speaker")
    assert store.devices.get("speaker", {}) == {}
    assert store.assignments["speaker"] == second.id
    assert store.voice_settings("speaker")["mode"] == "standard"
    assert store.voice_settings("speaker")["realtime_voice"] == "sage"
    assert store.resolve("speaker").voice_id == "sam"
    restored = SpeechStore(store.path.parent, ())
    assert restored.view("speaker") == store.view("speaker")
    assert restored.available_modes("speaker") == ["standard", "realtime"]


async def test_conflicts_reject_atomically_and_name_device(voice):
    store, first, second = voice
    store.record_capabilities("speaker", ["ws", "webrtc"])
    store.update({"agent_id": first.id, "mode": "realtime"}, "speaker")
    before = store.path.read_bytes()
    with pytest.raises(VoiceConflict, match="speaker.*reset"):
        store.update({"enabled_modes": ["standard"], "default_mode": "standard"}, agent_id=first.id)
    assert store.path.read_bytes() == before
    store.update({"enabled_modes": ["standard"], "default_mode": "standard"}, agent_id=second.id)
    before = store.path.read_bytes()
    with pytest.raises(VoiceConflict, match="speaker"):
        store.update({"agent_id": second.id}, "speaker")
    assert store.path.read_bytes() == before
    store.update({"agent_id": second.id, "reset": True}, "speaker")
    assert store.voice_settings("speaker")["mode"] == "standard"
    with pytest.raises(VoiceConflict):
        store.update({"mode": "realtime"}, "speaker")
    with pytest.raises(VoiceConflict, match="speaker: Chat-only"):
        store.update({"enabled_modes": []}, agent_id=second.id)


async def test_caps_intersection_and_no_fallback_on_reconnect_or_missing_agent(voice):
    store, first, _ = voice
    store.update({"agent_id": first.id}, "ws-only")
    assert store.available_modes("ws-only") == ["standard"]
    with pytest.raises(VoiceConflict):
        store.update({"mode": "realtime"}, "ws-only")
    store.record_capabilities("ws-only", ["webrtc"])
    store.update({"mode": "realtime"}, "ws-only")
    store.record_capabilities("ws-only", ["ws"])
    assert store.voice_settings("ws-only")["mode"] == "realtime"
    assert store.view("ws-only")["policy"]["conflict"]
    agents.remove(first.id)
    assert agents.get_for_device("ws-only") is None
    assert "unavailable" in store.conflict("ws-only")


async def test_authenticated_agent_api_and_runtime_admission(voice):
    store, first, _ = voice
    from vauxr.server import AppState, ConnectionCtx, _voice_start
    from vauxr.web.server import make_http_app

    path = f"/api/agents/{first.id}/speech"
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.patch(path, json={"enabled_modes": []})).status == 401
        headers = owner_headers(client)
        response = await client.patch(
            path, headers=headers, json={"enabled_modes": ["realtime"], "default_mode": "realtime"}
        )
        assert response.status == 200
        assert (await response.json())["policy"]["enabled_modes"] == ["realtime"]
        store.record_capabilities("speaker", ["webrtc"])
        store.update({"agent_id": first.id}, "speaker")
        response = await client.patch(
            "/api/devices/speaker/speech", headers=headers, json={"mode": "standard"}
        )
        assert response.status == 409
        assert "speaker" in (await response.json())["error"]
        ws = SimpleNamespace(closed=False, send_str=AsyncMock())
        await _voice_start(AppState(), ws, ConnectionCtx(device_id="speaker"), {})
        error = json.loads(ws.send_str.call_args.args[0])
        assert error["code"] == "VOICE_CONFLICT"


async def test_assigned_non_global_agent_owns_transcript_and_response(voice):
    store, _first, second = voice
    from vauxr.agents.server import AgentServer
    from vauxr.auth.service import authenticate

    seed("second-token", Role.INTEGRATION, second.id)
    server = AgentServer()
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock())
    connection = SimpleNamespace(ws=ws, principal=authenticate("second-token"), agent=second)
    server._connections[second.id] = connection
    store.update({"agent_id": second.id}, "speaker")
    delta = []
    listener = {
        "on_delta": lambda run, text: delta.append(text),
        "on_end": lambda run: None,
        "on_error": lambda run, error: None,
    }
    server.add_response_listener("speaker", listener)
    assert server.send_transcript("speaker", "hello")
    await asyncio.sleep(0)
    assert json.loads(ws.send_str.call_args.args[0])["deviceId"] == "speaker"
    await server._handle_authenticated_message(
        connection, {"type": "agent.response.delta", "deviceId": "speaker", "runId": "run", "text": "reply"}
    )
    assert delta == ["reply"]
    # Assignment never authorizes a different agent or another device's response.
    await server._handle_authenticated_message(
        connection, {"type": "agent.response.delta", "deviceId": "other", "runId": "run", "text": "wrong"}
    )
    assert delta == ["reply"]
    ws.close.assert_not_awaited()
    assert json.loads(ws.send_str.call_args.args[0])["code"] == "FORBIDDEN"


async def test_active_voice_rejects_route_change_until_stopped(voice):
    store, first, second = voice
    from vauxr.devices import registry

    registry.reset()
    store.update({"agent_id": first.id}, "speaker")
    registry.register("speaker", ws=SimpleNamespace(closed=False))
    registry.set_state("speaker", "speaking")
    before = store.path.read_bytes()
    with pytest.raises(VoiceConflict, match="speaker.*Stop voice"):
        store.update({"agent_id": second.id}, "speaker")
    assert store.path.read_bytes() == before
    registry.set_state("speaker", "idle")
    store.update({"agent_id": second.id}, "speaker")
    registry.reset()


async def test_offer_revalidates_mode_after_authenticated_wake(voice, monkeypatch):
    store, first, _ = voice
    from aiohttp import web

    from vauxr.realtime import session
    from vauxr.realtime.app import _offer_handler

    seed("speaker-token", Role.DEVICE, "speaker")
    store.record_capabilities("speaker", ["webrtc"])
    store.update({"agent_id": first.id, "mode": "realtime"}, "speaker")
    manager = SimpleNamespace(can_accept_offer=lambda _: True, _live_devices=set(), handle_offer=AsyncMock())
    monkeypatch.setattr(session, "get_manager", lambda: manager)
    app = web.Application()
    app.router.add_post("/api/offer", _offer_handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/offer",
            json={"device_id": "speaker", "token": "speaker-token", "sdp": "fixture", "type": "offer"},
        )
        assert response.status == 409
        assert "mode changed" in (await response.json())["error"]
    manager.handle_offer.assert_not_awaited()


async def test_assigned_live_request_correlates_non_global_agent(voice):
    store, _first, second = voice
    from vauxr.agents.server import AgentServer
    from vauxr.auth.service import authenticate

    seed("second-token", Role.INTEGRATION, second.id)
    server = AgentServer()
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock())
    connection = SimpleNamespace(ws=ws, principal=authenticate("second-token"), agent=second)
    server._connections[second.id] = connection
    store.update({"agent_id": second.id}, "speaker")
    task = asyncio.create_task(server.realtime_request(second.id, "speaker", "session", "bootstrap"))
    await asyncio.sleep(0)
    frame = json.loads(ws.send_str.call_args.args[0])
    assert frame["operation"] == "bootstrap" and frame["deviceId"] == "speaker"
    await server._handle_authenticated_message(
        connection,
        {
            "type": "agent.realtime.result",
            "deviceId": "speaker",
            "requestId": frame["requestId"],
            "result": {"instructions": "household"},
        },
    )
    assert await task == {"instructions": "household"}


async def test_direct_integration_cannot_enable_live(voice, monkeypatch):
    store, _first, _second = voice
    from vauxr.config import reset_config

    monkeypatch.setenv("OPENCLAW_URL", "ws://127.0.0.1:1")
    reset_config()
    agents._reset_for_tests()
    agents.load()
    assert store.integration_modes("openclaw-direct") == ["standard"]
    with pytest.raises(ValueError, match="integration does not support"):
        store.update({"enabled_modes": ["realtime"], "default_mode": "realtime"}, agent_id="openclaw-direct")


async def test_global_fallback_change_reports_incompatible_unassigned_device(voice):
    store, first, second = voice
    store.record_capabilities("legacy-speaker", ["ws"])
    store.update({"enabled_modes": ["realtime"], "default_mode": "realtime"}, agent_id=second.id)
    with pytest.raises(VoiceConflict, match="legacy-speaker"):
        store.validate_fallback(second.id)
    assert agents.get_active().id == first.id
    # An explicit assignment keeps that device independent of global activation.
    store.update({"agent_id": first.id}, "legacy-speaker")
    store.validate_fallback(second.id)


async def test_unassignment_does_not_reroute_active_media(voice):
    store, first, second = voice
    from vauxr.devices import registry

    registry.reset()
    store.update({"agent_id": first.id}, "speaker")
    agents.activate(second.id)
    registry.register("speaker", ws=SimpleNamespace(closed=False))
    registry.set_state("speaker", "speaking")
    with pytest.raises(VoiceConflict, match="Stop voice"):
        store.update({"agent_id": None}, "speaker")
    assert store.assignments["speaker"] == first.id
    registry.set_state("speaker", "idle")
    store.update({"agent_id": None}, "speaker")
    assert agents.get_for_device("speaker").id == second.id
    registry.reset()


async def test_transport_changes_preserve_inherited_mode_and_reject_live_over_ws(voice):
    store, first, _second = voice
    from vauxr.devices import registry
    from vauxr.web.server import make_http_app

    registry.reset()
    store.record_capabilities("speaker", ["webrtc"])
    store.update({"agent_id": first.id}, "speaker")
    registry.register("speaker", ws=SimpleNamespace(closed=False))
    async with TestClient(TestServer(make_http_app())) as client:
        headers = owner_headers(client)
        response = await client.patch(
            "/api/devices/speaker", headers=headers, json={"transport_mode": "websocket", "barge_in": True}
        )
        assert response.status == 200
        body = await response.json()
        assert body["config"]["voice_mode"] == "websocket"
        assert body["config"]["barge_in"] is False
        assert "mode" not in store.devices.get("speaker", {})
        with pytest.raises(VoiceConflict, match="WebRTC"):
            store.update({"mode": "realtime"}, "speaker")
        response = await client.patch(
            "/api/devices/speaker", headers=headers, json={"transport_mode": "webrtc"}
        )
        assert response.status == 200
        assert "mode" not in store.devices.get("speaker", {})
        store.update({"mode": "realtime"}, "speaker")
        before = store.path.read_bytes()
        response = await client.patch(
            "/api/devices/speaker", headers=headers, json={"transport_mode": "websocket"}
        )
        assert response.status == 409
        assert store.path.read_bytes() == before
        assert registry.get_config_for("speaker")["transport_mode"] == "webrtc"
    registry.reset()
