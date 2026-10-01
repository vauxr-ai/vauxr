"""Standard compatibility outside the realtime startup path."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import vauxr.devices.registry as registry
import vauxr.realtime.session as realtime_session
import vauxr.server as server
from vauxr.devices.config import load_device_configs, pipeline_mode, save_device_configs


def test_mode_persistence_and_default(tmp_path):
    save_device_configs(str(tmp_path), {"a": {"pipeline_mode": "realtime"},
                                        "b": {"pipeline_mode": []}})
    loaded = load_device_configs(str(tmp_path))
    assert pipeline_mode(loaded["a"]) == "realtime"
    assert pipeline_mode(loaded["b"]) == pipeline_mode(None) == "standard"


@pytest.fixture
def env(monkeypatch):
    registry.reset()
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    monkeypatch.setattr(server, "_authorize_message", AsyncMock(return_value=True))
    monkeypatch.setattr(server, "resolve", lambda _: Mock())
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("dev", ws=ws)
    ctx = server.ConnectionCtx(device_id="dev")
    yield manager, ws, ctx
    registry.reset()


async def send(ws, ctx, message):
    await server.handle_text(server.AppState(), ws, ctx, json.dumps(message))


async def test_interrupted_standard_cleanup_does_not_reset_new_capture(env, monkeypatch):
    _, ws, ctx = env
    finish = asyncio.Event()

    async def run(*args, **kwargs):
        await finish.wait()
    monkeypatch.setattr(server, "run_voice_turn", run)
    await send(ws, ctx, {"type": "voice.start"})
    await send(ws, ctx, {"type": "voice.end"})
    await asyncio.sleep(0)
    await send(ws, ctx, {"type": "voice.start"})
    finish.set()
    await asyncio.sleep(0)
    assert ctx.state == server.ConnectionState.LISTENING


@pytest.mark.parametrize("enabled,host,caps,webrtc", [
    (True, "vauxr.local", ["webrtc"], True),
    (False, "vauxr.local", ["webrtc"], False),
    (True, "", ["webrtc"], False),
    (True, "vauxr.local", ["ws"], False),
])
async def test_standard_transport_policy(env, monkeypatch, enabled, host, caps, webrtc):
    import vauxr.auth.owner as owner
    _, ws, ctx = env
    monkeypatch.setattr(owner, "configured_origin", lambda: "https://vauxr.test")
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=enabled, host=host, offer_path="/api/offer", stun_url="")))
    await server._hello(ws, ctx, {"caps": caps})
    hello = json.loads(ws.send_str.call_args.args[0])
    assert hello["pipeline_mode"] == "standard"
    assert hello["realtime"]["enabled"] is webrtc
    assert hello["realtime"]["transport"] == ("webrtc" if webrtc else "ws")


async def test_websocket_device_policy_opts_out_of_available_webrtc(env, monkeypatch):
    import vauxr.auth.owner as owner
    _, ws, ctx = env
    monkeypatch.setattr(registry, "get_config_for", lambda _: {"transport_mode": "websocket"})
    monkeypatch.setattr(owner, "configured_origin", lambda: "https://vauxr.test")
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=True, host="vauxr.local", offer_path="/api/offer", stun_url="")))
    await server._hello(ws, ctx, {"caps": ["webrtc"]})
    hello = json.loads(ws.send_str.call_args.args[0])
    assert hello["pipeline_mode"] == "standard"
    assert hello["realtime"] == {"enabled": False, "transport": "ws"}


async def test_standard_webrtc_requires_streaming_startup_id(env, monkeypatch):
    manager, ws, ctx = env
    monkeypatch.setattr(server, "current", lambda _: True)
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=True, host="vauxr.local")))
    await server._realtime_start(server.AppState(), ws, ctx, {})
    assert not ctx.realtime
    assert not manager.can_accept_offer("dev")
    assert json.loads(ws.send_str.call_args.args[0])["code"] == "REALTIME_STARTUP_REQUIRED"


@pytest.mark.parametrize("message", [{"startup_id": 1}, {"mode": "live"}])
async def test_websocket_policy_rejects_realtime_start(env, monkeypatch, message):
    manager, ws, ctx = env
    monkeypatch.setattr(server, "current", lambda _: True)
    monkeypatch.setattr(registry, "get_config_for", lambda _: {"transport_mode": "websocket"})
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=True, host="vauxr.local")))
    await server._realtime_start(server.AppState(), ws, ctx, message)
    assert json.loads(ws.send_str.call_args.args[0])["code"] == "REALTIME_UNAVAILABLE"
    assert not ctx.realtime and ctx.startup is None
    assert not manager.can_accept_offer("dev")
    assert registry.get("dev").state == "idle"


async def test_explicit_browser_live_still_arms_without_a_startup_id(env, monkeypatch):
    from vauxr.speech import store as speech_store

    manager, ws, ctx = env
    monkeypatch.setattr(server, "current", lambda _: True)
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=True, host="vauxr.local")))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(speech_store, "get_store", lambda: SimpleNamespace(
        voice_settings=lambda _: {"mode": "realtime"}))
    await server._realtime_start(server.AppState(), ws, ctx, {"mode": "live"})
    assert json.loads(ws.send_str.call_args.args[0]) == {"type": "realtime.armed"}
    assert ctx.realtime and ctx.realtime_media and ctx.startup is None
    assert manager.can_accept_offer("dev") and "dev" in manager._live_devices
    await manager.stop_all()


async def test_dropped_active_peer_allows_next_standard_speech(env):
    manager, ws, ctx = env
    manager._live_devices.add("dev")
    ctx.realtime = ctx.realtime_media = True
    await send(ws, ctx, {"type": "voice.start"})
    assert ctx.state == server.ConnectionState.LISTENING
    assert not ctx.realtime_media
    assert not manager.can_accept_offer("dev")
