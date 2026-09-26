"""Queue races and lifecycle fencing at the actual startup/session boundary."""
import asyncio
import numpy as np
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pipecat.frames.frames import InputAudioRawFrame

import vauxr.devices.registry as registry
import vauxr.realtime.session as realtime_session
from vauxr.realtime.startup import StartupAudio


async def test_rtp_arriving_during_drain_cannot_overtake_ws_or_previous_rtp():
    startup = StartupAudio(1, AsyncMock())
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def consume(frame):
        seen.append(frame.audio)
        if len(seen) == 1:
            entered.set()
            await release.wait()

    startup.append_ws(0, bytes(320 * 2))
    one = InputAudioRawFrame(b"\x01\x00" * 480, 24000, 1)
    two = InputAudioRawFrame(b"\x02\x00" * 480, 24000, 1)
    startup.append_rtp(one)
    startup.bind(consume)
    startup.ready()
    startup.finish_ws(1, 1)
    await entered.wait()
    assert startup.append_rtp(two)
    release.set()
    await startup._drain_task
    assert len(seen[0]) == 960
    assert max(abs(np.frombuffer(seen[0], dtype="<i2"))) <= 1  # SoX integer dither
    assert seen[1:] == [one.audio, two.audio]
    assert not startup.append_rtp(one)
    startup.close()


async def test_cancel_during_drain_discards_every_remaining_frame():
    failed = AsyncMock()
    startup = StartupAudio(1, failed)
    entered, blocked = asyncio.Event(), asyncio.Event()
    seen = []

    async def consume(frame):
        seen.append(frame.audio)
        entered.set()
        await blocked.wait()

    startup.append_ws(0, bytes(640 * 2))
    startup.append_rtp(InputAudioRawFrame(bytes(960), 24000, 1))
    startup.bind(consume)
    startup.ready()
    startup.finish_ws(1, 1)
    await entered.wait()
    startup.close()
    await asyncio.gather(startup._drain_task, return_exceptions=True)
    assert len(seen) == 1 and not startup._rtp and not startup._ws
    failed.assert_not_called()


async def test_slow_old_cleanup_cannot_discard_or_idle_replacement(monkeypatch):
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("race", ws=ws)
    old = StartupAudio(1, AsyncMock())
    manager.begin_startup("race", old)
    session = realtime_session.RealtimeSession("race", None)
    session._startup = old
    entered, release = asyncio.Event(), asyncio.Event()

    async def disconnect():
        entered.set()
        await release.wait()

    session._connection = SimpleNamespace(disconnect=disconnect)
    manager._sessions["race"] = session
    stopping = asyncio.create_task(manager.abort_wake("race"))
    await entered.wait()
    replacement = StartupAudio(2, AsyncMock())
    manager.begin_startup("race", replacement)
    newer = realtime_session.RealtimeSession("race", None)
    newer._startup = replacement
    manager._sessions["race"] = newer
    registry.set_state("race", "listening")
    replacement.append_ws(0, bytes(640))
    release.set()
    await stopping
    assert old.closed and not replacement.closed
    assert manager._sessions["race"] is newer
    assert manager._startups["race"] is replacement
    assert replacement.next_seq == 1 and registry.get("race").state == "listening"
    ws.send_str.assert_not_called()
    await manager.stop("race")
    registry.unregister("race")


async def test_delayed_offer_failure_cannot_abort_new_wake(monkeypatch):
    manager = realtime_session.RealtimeManager()
    old = StartupAudio(1, AsyncMock())
    manager.begin_startup("race", old)
    wake = manager._wake_generations["race"]
    replacement = StartupAudio(2, AsyncMock())
    manager.begin_startup("race", replacement)
    await manager.abort_wake("race", expected_wake=wake)
    assert old.closed and not replacement.closed
    assert manager._startups["race"] is replacement
    await manager.stop("race")


async def test_provider_buffer_overflow_and_consumer_failure_discard_audio():
    for overflow in (True, False):
        failed = AsyncMock()
        startup = StartupAudio(1, failed)
        if overflow:
            startup.append_rtp(InputAudioRawFrame(bytes(StartupAudio.MAX_AUDIO_SECONDS * 48000 + 2), 24000, 1))
        else:
            startup.append_ws(0, bytes(640))
            startup.bind(AsyncMock(side_effect=ConnectionError("provider stopped")))
            startup.ready()
            startup.finish_ws(1, 1)
            await startup._drain_task
        await asyncio.sleep(0)
        assert startup.closed and not startup._ws and not startup._rtp
        failed.assert_awaited_once_with("REALTIME_STARTUP_OVERFLOW" if overflow else "REALTIME_STARTUP_FAILED")


async def test_old_session_loses_controls_as_soon_as_new_wake_or_socket_owns_device(monkeypatch):
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("race", ws=ws)
    startup = StartupAudio(1, AsyncMock())
    manager.begin_startup("race", startup)
    old = realtime_session.RealtimeSession("race", None)
    old._startup = startup
    manager._sessions["race"] = old
    assert old._owns_control()
    newer_ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("race", ws=newer_ws)
    assert not old._owns_control()
    registry.register("race", ws=ws)
    replacement = StartupAudio(2, AsyncMock())
    manager.begin_startup("race", replacement)
    assert not old._owns_control()  # Even before the replacement offer arrives.
    await old._send_control({"type": "audio.end", "follow_up": False})
    ws.send_str.assert_not_called()
    newer_ws.send_str.assert_not_called()
    await manager.stop("race")
    registry.unregister("race")


async def test_peer_close_preserves_new_startup_before_its_offer(monkeypatch):
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    old = StartupAudio(1, AsyncMock())
    manager.begin_startup("race", old)
    session = realtime_session.RealtimeSession("race", None)
    session._startup = old
    manager._sessions["race"] = session
    entered, release = asyncio.Event(), asyncio.Event()

    async def disconnect():
        entered.set()
        await release.wait()

    session._connection = SimpleNamespace(disconnect=disconnect)
    closing = asyncio.create_task(session.close())
    await entered.wait()
    replacement = StartupAudio(2, AsyncMock())
    manager.begin_startup("race", replacement)
    release.set()
    await closing
    assert manager._startups["race"] is replacement
    assert not replacement.closed
    assert manager.can_accept_offer("race")
    await manager.stop("race")
