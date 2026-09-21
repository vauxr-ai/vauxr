"""Standard-first policy and playback handoff regressions without media dependencies."""
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
    ctx = server.ConnectionCtx(device_id="dev", realtime=True)
    yield manager, ws, ctx
    registry.reset()


async def send(ws, ctx, message):
    await server.handle_text(server.AppState(), ws, ctx, json.dumps(message))


async def test_completed_standard_turn_waits_for_playback_before_live_offer(env, monkeypatch):
    manager, ws, ctx = env

    async def run(*args, **kwargs):
        manager.record_turn("dev", "switch on the lamp", "The lamp is on.")
        args[2].turn_completed()
    pipeline = AsyncMock(side_effect=run)
    monkeypatch.setattr(server, "run_voice_turn", pipeline)
    await send(ws, ctx, {"type": "voice.start"})
    await send(ws, ctx, {"type": "voice.end"})
    await asyncio.sleep(0)
    assert pipeline.await_count == 1
    assert ctx.playback_pending == ctx.turn_id
    assert not manager.can_accept_offer("dev")
    assert not ctx.realtime_media
    await send(ws, ctx, {"type": "audio.playback_complete", "turn_id": ctx.turn_id})
    assert manager.can_accept_offer("dev")
    # Accept a correlated device receipt once; the provider owns its own
    # pre-session audio gate rather than requiring a second device receipt.
    session = SimpleNamespace(is_peer_live=lambda: True, _handoff_pending=True,
                              _live_service=SimpleNamespace(_session_started=False))
    manager._sessions["dev"] = session
    await send(ws, ctx, {"type": "realtime.media_ready", "turn_id": ctx.turn_id})
    assert ctx.realtime_media and not ctx.handoff_ready
    assert not session._handoff_pending
    session._live_service._session_started = True
    assert ctx.realtime_media and not session._handoff_pending
    assert pipeline.await_count == 1


async def test_next_speech_invalidates_old_playback_ack(env):
    manager, ws, ctx = env
    manager.prepare_handoff = Mock(return_value=True)
    ctx.playback_pending = ctx.turn_id = 1
    await send(ws, ctx, {"type": "voice.start"})
    await send(ws, ctx, {"type": "audio.playback_complete", "turn_id": 1})
    await send(ws, ctx, {"type": "realtime.media_ready", "turn_id": 1})
    manager.prepare_handoff.assert_not_called()
    assert ctx.state == server.ConnectionState.LISTENING
    assert not ctx.realtime_media


async def test_init_failure_keeps_standard(env):
    manager, ws, ctx = env
    ctx.playback_pending = ctx.turn_id = 1
    await send(ws, ctx, {"type": "audio.playback_complete", "turn_id": 1})
    await manager.abort_wake("dev")
    await send(ws, ctx, {"type": "realtime.media_ready", "turn_id": 1})
    assert not ctx.realtime_media
    await send(ws, ctx, {"type": "voice.start"})
    assert ctx.state == server.ConnectionState.LISTENING


async def test_interrupted_standard_cleanup_does_not_reset_new_capture(env, monkeypatch):
    _, ws, ctx = env
    ctx.realtime = False
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


async def test_hello_standard_default_even_when_server_supports_realtime(env, monkeypatch):
    _, ws, ctx = env
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=True, host="vauxr.local")))
    await server._hello(ws, ctx, {"caps": ["webrtc"]})
    hello = json.loads(ws.send_str.call_args.args[0])
    assert hello["pipeline_mode"] == "standard"
    assert hello["realtime"] == {"enabled": False, "transport": "ws"}


async def test_standard_mode_cannot_arm_realtime(env, monkeypatch):
    manager, ws, ctx = env
    ctx.realtime = False
    monkeypatch.setattr(server, "get_config", lambda: SimpleNamespace(
        realtime=SimpleNamespace(enabled=True, host="vauxr.local")))
    await server._realtime_start(server.AppState(), ws, ctx, {})
    assert not ctx.realtime
    assert not manager.can_accept_offer("dev")


async def test_failed_initializing_peer_does_not_end_standard_playback(env):
    manager, ws, _ = env
    session = realtime_session.RealtimeSession("dev", None)
    session._handoff_pending = True
    manager._sessions["dev"] = session
    await session._notify_ended()
    ws.send_str.assert_not_called()
    assert session._handoff_pending


async def test_ended_wake_rejects_stale_ack_and_media_ready(env):
    manager, ws, ctx = env
    ctx.playback_pending = ctx.turn_id = 1
    await send(ws, ctx, {"type": "realtime.stop"})
    await send(ws, ctx, {"type": "audio.playback_complete", "turn_id": 1})
    await send(ws, ctx, {"type": "realtime.media_ready", "turn_id": 1})
    assert not ctx.realtime and not ctx.realtime_media
    assert not manager.can_accept_offer("dev")


@pytest.mark.parametrize("outcome", ["error", "empty", "exception"])
async def test_unsuccessful_standard_turn_never_admits_handoff(env, monkeypatch, outcome):
    manager, ws, ctx = env
    async def run(*args, **kwargs):
        if outcome == "exception":
            raise RuntimeError("provider failed")
        if outcome == "error":
            await args[2].send_str(json.dumps({"type": "error", "code": "TTS_ERROR"}))
            args[2].turn_completed()
    monkeypatch.setattr(server, "run_voice_turn", run)
    await send(ws, ctx, {"type": "voice.start"})
    await send(ws, ctx, {"type": "voice.end"})
    await asyncio.sleep(0)
    assert ctx.playback_pending is None
    assert not manager.can_accept_offer("dev")
    await send(ws, ctx, {"type": "voice.start"})
    assert ctx.state == server.ConnectionState.LISTENING


async def test_next_speech_cancels_admitted_handoff(env):
    manager, ws, ctx = env
    ctx.playback_pending = ctx.turn_id = 1
    await send(ws, ctx, {"type": "audio.playback_complete", "turn_id": 1})
    assert manager.can_accept_offer("dev")
    await send(ws, ctx, {"type": "voice.start"})
    assert not manager.can_accept_offer("dev")
    await send(ws, ctx, {"type": "realtime.media_ready", "turn_id": 1})
    assert not ctx.realtime_media


async def test_dropped_active_peer_allows_next_standard_speech(env):
    manager, ws, ctx = env
    manager._live_devices.add("dev")
    ctx.realtime_media = True
    await send(ws, ctx, {"type": "voice.start"})
    assert ctx.state == server.ConnectionState.LISTENING
    assert not ctx.realtime_media
    assert not manager.can_accept_offer("dev")
