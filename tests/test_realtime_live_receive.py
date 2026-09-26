"""Characterize pinned Live receive coupling; no external provider or captured content."""
from __future__ import annotations

import asyncio
import base64
import json
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")
from pipecat.frames.frames import Frame, LLMRunFrame, SpeechOutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import RawAudioTrack
from pipecat.workers.runner import WorkerRunner
from websockets.asyncio.server import ServerConnection, serve

import vauxr.devices.registry as device_registry
import vauxr.realtime.session as realtime_session
from vauxr.realtime.live import LiveService
from vauxr.realtime.session import RealtimeManager, RealtimeSession


async def test_installed_live_config_leaves_barge_in_with_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    live = LiveService(SimpleNamespace(_send_control=AsyncMock(), is_physical_live=False), "selected", {
        "realtime_model": "gpt-live-1", "realtime_voice": "cedar",
    })
    live._context = LLMContext()
    send = AsyncMock()
    monkeypatch.setattr(live, "send_client_event", send)
    await live._send_session_config()
    config = send.call_args.args[0].to_payload()["session"]
    # This is Live's actual wire config, not Realtime's server_vad defaults.
    assert set(config) == {"model", "audio", "delegation"}
    assert config["audio"] == {"output": {"voice": "cedar"}}
    assert config["delegation"] == {"type": "client"}
    assert not live.service_metadata_frame().user_turn_strategies.enable_interruptions
    user, _ = LLMContextAggregatorPair(LLMContext())
    assert user._params.vad_analyzer is None
    assert TransportParams().audio_out_auto_silence is True


async def test_blocked_browser_transcript_send_does_not_hold_provider_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider PCM must flow while the independent browser transcript writer is held.

    Only the browser WS writer is held. The installed receive loop, transcript
    override and RealtimeSession control path run unchanged against a local peer.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    manager = RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    entered, release, sent, received = (asyncio.Event() for _ in range(4))

    async def slow_write(_message: str) -> None:
        entered.set()
        await release.wait()

    session = RealtimeSession("receive-probe", SimpleNamespace(realtime_request=AsyncMock(return_value={})))
    manager._sessions[session.device_id] = session
    device_registry.register(session.device_id, ws=SimpleNamespace(closed=False, send_str=slow_write))
    live = LiveService(session, "selected", {"realtime_model": "gpt-live-1", "realtime_voice": "cedar"})
    track = RawAudioTrack(24000)
    audio_writes: list[asyncio.Future[bool]] = []
    errors: list[object] = []

    class AudioTap(FrameProcessor):
        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, SpeechOutputAudioRawFrame):
                audio_writes.append(track.add_audio_bytes(frame.audio))
                received.set()
            await self.push_frame(frame, direction)

    async def provider(ws: ServerConnection) -> None:
        async for raw in ws:
            if json.loads(raw)["type"] == "session.start":
                await ws.send(json.dumps({"type": "session.started", "session": {"id": "local"}}))
                await ws.send(json.dumps({"type": "session.output_transcript.delta", "delta": "synthetic"}))
                await ws.send(json.dumps({"type": "session.output_audio.delta",
                    "delta": base64.b64encode(b"\x11\x11" * 240).decode()}))
                sent.set()

    user, assistant = LLMContextAggregatorPair(LLMContext())
    worker = PipelineWorker(Pipeline([user, live, AudioTap(), assistant]), enable_rtvi=False)

    @worker.event_handler("on_pipeline_error")
    async def error(_worker: PipelineWorker, frame: object) -> None:
        errors.append(frame)

    runner = WorkerRunner(handle_sigint=False)
    async with serve(provider, "127.0.0.1", 0) as server:
        live.base_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        await runner.add_workers(worker)
        running = asyncio.create_task(runner.run())
        try:
            await worker.queue_frame(LLMRunFrame())
            await asyncio.wait_for(sent.wait(), 5)
            await asyncio.wait_for(entered.wait(), 5)
            # This fails on 0045d56: the installed provider receive loop is
            # stuck awaiting the browser transcript writer until release.
            await asyncio.wait_for(received.wait(), .5)
            assert not release.is_set()
            resumed = await track.recv()
            assert resumed.to_ndarray().any()
            assert audio_writes[0].done()
            release.set()
            assert not errors
        finally:
            release.set()
            await runner.cancel()
            await asyncio.wait_for(running, 5)
            if live.flush_task:
                live.flush_task.cancel()
                await asyncio.gather(live.flush_task, return_exceptions=True)
            track.stop()
            device_registry.unregister(session.device_id)
            manager._sessions.pop(session.device_id, None)
