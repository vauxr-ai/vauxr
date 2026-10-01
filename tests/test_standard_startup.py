"""Standard WebRTC uses the same ordered admission without selecting Live."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
pytest.importorskip("pipecat")
from pipecat.frames.frames import InputAudioRawFrame, StartFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

import vauxr.realtime.session as sessions
from vauxr.realtime.startup import StartupAudio
from vauxr.realtime.startup_input import StartupInput


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


async def test_standard_gate_replays_ws_before_early_rtp_at_16k():
    failed = AsyncMock()
    startup = StartupAudio(1, failed, input_rate=16000)
    ws = b"\x01\x02" * 640
    startup.append_ws(0, ws)
    first = InputAudioRawFrame(b"\x03\x04" * 320, 16000, 1)
    second = InputAudioRawFrame(b"\x05\x06" * 320, 16000, 1)
    seen, started = [], asyncio.Event()

    class Tap(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, StartFrame):
                started.set()
            elif isinstance(frame, InputAudioRawFrame):
                seen.append(frame)
            await self.push_frame(frame, direction)

    worker = PipelineWorker(Pipeline([StartupInput(startup), Tap()]), enable_rtvi=False,
                            params=PipelineParams(audio_in_sample_rate=16000))
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    running = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), 3)
        await worker.queue_frame(first)
        await until(lambda: len(startup._rtp) == 1)
        assert not seen
        startup.finish_ws(1, 1)
        await until(lambda: startup.drained and len(seen) == 3)
        await worker.queue_frame(second)
        await until(lambda: len(seen) == 4)
        assert b"".join(frame.audio for frame in seen) == ws + first.audio + second.audio
        assert all(frame.sample_rate == 16000 for frame in seen)
        assert startup._bytes_24k == 0
        failed.assert_not_called()
    finally:
        await runner.cancel()
        await asyncio.wait_for(running, 3)
    assert startup.closed


async def test_standard_wake_cleanup_and_retry_admission(monkeypatch):
    manager = sessions.RealtimeManager()
    monkeypatch.setattr(sessions, "_manager", manager)
    startup = StartupAudio(1, AsyncMock(), input_rate=16000)
    manager.begin_startup("standard", startup, live=False)
    assert manager.can_accept_offer("standard")
    assert "standard" not in manager._live_devices
    # A failed peer may retire its one-shot buffer, but the authenticated wake
    # still admits another SDP attempt until abort/revocation retires the wake.
    startup.close()
    manager._startups.pop("standard")
    assert manager.can_accept_offer("standard")
    await manager.stop_all()
    assert not manager.can_accept_offer("standard")


async def test_stop_all_closes_standard_startup_without_a_peer(monkeypatch):
    manager = sessions.RealtimeManager()
    monkeypatch.setattr(sessions, "_manager", manager)
    startup = StartupAudio(1, AsyncMock(), input_rate=16000)
    manager.begin_startup("standard", startup, live=False)
    await manager.stop_all()
    assert startup.closed and not manager.can_accept_offer("standard")


async def test_standard_pipeline_receives_startup_and_mic_during_playback(monkeypatch):
    import vauxr.config as config
    import vauxr.devices.registry as registry
    import vauxr.realtime.live as live
    import vauxr.realtime.wyoming as wyoming
    import pipecat.transports.smallwebrtc.transport as transport_module

    monkeypatch.setenv("REALTIME_ESP32", "0")
    config.reset_config()
    manager = sessions.RealtimeManager()
    monkeypatch.setattr(sessions, "_manager", manager)
    live_start = AsyncMock(side_effect=AssertionError("Standard must not select Live"))
    monkeypatch.setattr(live, "start_live", live_start)
    received, ready = [], asyncio.Event()

    class Pass(FrameProcessor):
        def __init__(self, **kwargs):
            super().__init__()

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class STT(Pass):
        async def process_frame(self, frame, direction):
            if direction == FrameDirection.DOWNSTREAM:
                if isinstance(frame, StartFrame):
                    ready.set()
                elif isinstance(frame, InputAudioRawFrame):
                    received.append(frame)
            await super().process_frame(frame, direction)

    class Transport:
        def __init__(self, **kwargs):
            self._input, self._output = Pass(), Pass()
            self._output._client = SimpleNamespace(_audio_output_track=None)

        def input(self):
            return self._input

        def output(self):
            return self._output

        def event_handler(self, _name):
            return lambda fn: fn

    monkeypatch.setattr(transport_module, "SmallWebRTCTransport", Transport)
    monkeypatch.setattr(wyoming, "WyomingSTTService", STT)
    monkeypatch.setattr(wyoming, "WyomingTTSService", Pass)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("standard-pipeline", ws=ws)
    startup = StartupAudio(1, AsyncMock(), input_rate=16000)
    startup.append_ws(0, b"\x01\x00" * 320)
    manager.begin_startup("standard-pipeline", startup, live=False)
    session = sessions.RealtimeSession("standard-pipeline", SimpleNamespace())
    session._startup = startup
    manager._sessions[session.device_id] = session
    try:
        await session.start(SimpleNamespace(disconnect=AsyncMock()))
        await asyncio.wait_for(ready.wait(), 3)
        startup.finish_ws(1, 1)
        await until(lambda: startup.drained and len(received) == 1)
        assert received[0].audio == b"\x01\x00" * 320
        assert received[0].sample_rate == 16000
        session._on_bot_started_speaking()
        assert not session._turns_suppressed()
        during_reply = InputAudioRawFrame(b"\x02\x00" * 320, 16000, 1)
        await session._task.queue_frame(during_reply)
        await until(lambda: len(received) == 2)
        assert received[1].audio == during_reply.audio
        live_start.assert_not_called()
    finally:
        await session.close()
        if session._runner_task:
            await asyncio.wait_for(session._runner_task, 3)
        registry.unregister(session.device_id)
        config.reset_config()
