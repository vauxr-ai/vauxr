"""Run the installed Pipecat Live service against a local wire-protocol peer, no paid API."""
import asyncio
import base64
import json
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")
from websockets.asyncio.server import serve
from pipecat.frames.frames import InputAudioRawFrame, LLMRunFrame, OutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from realtime_live import LiveService


async def test_installed_live_audio_delegation_recording_and_shutdown(monkeypatch):
    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    wire, operations, output_audio = [], [], []
    started, delegated, release, spoken, received_mic, disconnected = (asyncio.Event() for _ in range(6))

    async def request(agent, device, session, operation, payload, timeout):
        assert (agent, device) == ("selected", "browser")
        operations.append((operation, payload))
        if operation == "consult":
            delegated.set()
            await release.wait()
            return {"text": "The backend action completed once."}
        return {"recorded": len(payload["fragments"])}

    session = SimpleNamespace(_agent_server=SimpleNamespace(realtime_request=request), device_id="browser",
        _send_control=AsyncMock(), _touch_activity=lambda: None, close=AsyncMock())
    llm = LiveService(session, "selected", {"realtime_model": "gpt-live-1", "realtime_voice": "cedar"})

    class AudioSink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, OutputAudioRawFrame):
                output_audio.append(frame.audio)
            await self.push_frame(frame, direction)

    async def provider(ws):
        try:
            async for raw in ws:
                event = json.loads(raw); wire.append(event)
                if event["type"] == "session.start":
                    assert event["session"]["model"] == "gpt-live-1"
                    assert event["session"]["delegation"]["type"] == "client"
                    await ws.send(json.dumps({"type": "session.started", "session": {"id": "local"}}))
                    for event in [
                        {"type": "session.input_transcript.delta", "delta": "Turn on the lamp"},
                        {"type": "session.output_transcript.delta", "delta": "I will check."},
                        {"type": "session.output_audio.delta", "delta": base64.b64encode(bytes(960)).decode()},
                        {"type": "session.delegation.created", "delegation": {"id": "one", "target": "client"}},
                    ]:
                        await ws.send(json.dumps(event))
                    started.set()
                elif event["type"] == "session.input_audio.append":
                    received_mic.set()
                elif event["type"] == "session.commentary.append":
                    spoken.set()
        finally:
            disconnected.set()

    async with serve(provider, "127.0.0.1", 0) as server:
        llm.base_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        context = LLMContext(messages=[{"role": "system", "content": "Selected backend profile"}])
        user, assistant = LLMContextAggregatorPair(context)
        worker = PipelineWorker(Pipeline([user, llm, AudioSink(), assistant]), enable_rtvi=False,
            params=PipelineParams(audio_in_sample_rate=24000, audio_out_sample_rate=24000))
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        running = asyncio.create_task(runner.run())
        try:
            await worker.queue_frame(LLMRunFrame())
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.wait_for(delegated.wait(), 5)
            # Input still flows while provider output and backend work overlap.
            await worker.queue_frame(InputAudioRawFrame(bytes(960), 24000, 1))
            await asyncio.wait_for(received_mic.wait(), 5)
            assert output_audio
            assert operations[0][0] == "record"
            assert "Turn on the lamp" not in operations[1][1]["request"], "do not replay transcript as new prompt"
            assert not spoken.is_set()
            release.set()
            await asyncio.wait_for(spoken.wait(), 5)
            assert len([op for op, _ in operations if op == "consult"]) == 1
            assert any(not f["delivered"] for op, payload in operations if op == "record" for f in payload["fragments"] if f["role"] == "assistant")
        finally:
            release.set()
            if llm.flush_task:
                llm.flush_task.cancel()
            await runner.cancel()
            await asyncio.wait_for(running, 5)
            await asyncio.wait_for(disconnected.wait(), 5)
        assert llm._websocket is None
