"""Pinned output-path probes, including real aiortc Opus/RTP consumption.

Only the DTLS network boundary is replaced. These tests cannot establish browser
playout, acoustic echo, or packet delivery across a real network.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("pipecat")
import numpy as np
from av import AudioFrame
from aiortc import RTCRtpSender
from aiortc.codecs.opus import OpusDecoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtp import RtpPacket, is_rtcp
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpSendParameters
from pipecat.frames.frames import (
    BotStartedSpeakingFrame, BotStoppedSpeakingFrame, ErrorFrame, Frame, InterruptionFrame,
    OutputAudioRawFrame, StartFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import (
    RawAudioTrack, SmallWebRTCClient, SmallWebRTCOutputTransport,
)
from pipecat.workers.runner import WorkerRunner

from realtime_live import LiveService


async def until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


def tone(frequency: int, milliseconds: int) -> bytes:
    samples = np.arange(24 * milliseconds)
    return (12000 * np.sin(2 * np.pi * frequency * samples / 24000)).astype("<i2").tobytes()


@asynccontextmanager
async def output_probe(
    monkeypatch: pytest.MonkeyPatch, *, timeout: float = 10,
) -> AsyncIterator[SimpleNamespace]:
    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    session = SimpleNamespace(
        device_id="browser", _touch_activity=lambda: None, _send_control=AsyncMock(),
        _agent_server=SimpleNamespace(realtime_request=AsyncMock(return_value={})),
    )
    live = LiveService(session, "selected", {"realtime_model": "gpt-live-1", "realtime_voice": "cedar"})
    # Feed the real provider event handlers, without opening a paid connection.
    monkeypatch.setattr(live, "_connect", AsyncMock())
    track = RawAudioTrack(24000)  # Production auto-silence remains enabled.
    consumed, writes, packets, events, errors = [], [], [], [], []
    ready = asyncio.Event()
    network_blocked = asyncio.Event()
    network = asyncio.Event()
    network.set()
    original_recv = track.recv
    original_add = track.add_audio_bytes

    async def recv() -> AudioFrame:
        frame = await original_recv()
        consumed.append(frame)
        return frame

    def add(audio: bytes) -> asyncio.Future[bool]:
        future = original_add(audio)
        writes.append((audio, future))
        return future

    track.recv = recv
    track.add_audio_bytes = add
    client = SimpleNamespace(
        _audio_output_track=track, _can_send=lambda: True, setup=AsyncMock(),
        connect=AsyncMock(), disconnect=AsyncMock(), send_message=AsyncMock(),
    )

    async def write(frame: OutputAudioRawFrame) -> bool:
        return await SmallWebRTCClient.write_audio_frame(client, frame)

    client.write_audio_frame = write
    output = SmallWebRTCOutputTransport(client, TransportParams(
        audio_out_enabled=True, audio_out_write_timeout_secs=timeout,
    ))

    class Tap(FrameProcessor):
        async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            if direction == FrameDirection.DOWNSTREAM:
                events.append(frame)
                if isinstance(frame, StartFrame):
                    ready.set()
            await self.push_frame(frame, direction)

    user, assistant = LLMContextAggregatorPair(LLMContext())
    worker = PipelineWorker(Pipeline([user, live, output, Tap(), assistant]), enable_rtvi=False,
        params=PipelineParams(audio_in_sample_rate=24000, audio_out_sample_rate=24000))

    @worker.event_handler("on_pipeline_error")
    async def error(_worker: PipelineWorker, frame: ErrorFrame) -> None:
        errors.append(frame)

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    running = asyncio.create_task(runner.run())

    async def send_rtp(data: bytes) -> None:
        if not is_rtcp(data):
            if not network.is_set():
                network_blocked.set()
            await network.wait()
            packets.append(RtpPacket.parse(data))

    dtls = SimpleNamespace(state="connected", _register_rtp_sender=Mock(),
        _unregister_rtp_sender=Mock(), _send_rtp=send_rtp)
    sender = RTCRtpSender(track, dtls)
    probe = SimpleNamespace(live=live, track=track, consumed=consumed, writes=writes,
        packets=packets, events=events, errors=errors, network=network, worker=worker,
        output=output, sender=sender, network_blocked=network_blocked)
    try:
        await asyncio.wait_for(ready.wait(), 5)
        yield probe
    finally:
        network.set()
        await sender.stop()
        await runner.cancel()
        await asyncio.wait_for(running, 5)
        if live.flush_task:
            live.flush_task.cancel()
            await asyncio.gather(live.flush_task, return_exceptions=True)
        track.stop()


async def start_rtp(probe: SimpleNamespace) -> None:
    await probe.sender.send(RTCRtpSendParameters(codecs=[RTCRtpCodecParameters(
        mimeType="audio/opus", clockRate=48000, channels=2, payloadType=111,
    )]))


async def audio(probe: SimpleNamespace, pcm: bytes) -> None:
    await probe.live._handle_evt_audio_delta(SimpleNamespace(delta=base64.b64encode(pcm).decode()))


async def test_live_pcm_survives_rtp_backpressure_and_overlapping_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with output_probe(monkeypatch) as p:
        expected = tone(440, 320) + tone(880, 320) + tone(1320, 320)
        await audio(p, expected)
        await until(lambda: len(p.writes) == 1)
        # A stopped consumer holds the first 40ms write; output stays queued.
        assert not p.writes[0][1].done()
        assert p.consumed == []
        await start_rtp(p)
        await until(lambda: len(p.packets) >= 5)
        p.network.clear()
        await asyncio.wait_for(p.network_blocked.wait(), 5)
        await until(lambda: not p.writes[-1][1].done())
        count = len(p.writes)
        await asyncio.sleep(0.12)
        assert len(p.writes) == count
        assert not p.writes[-1][1].done()
        # Provider-owned overlap must not become a pipeline queue-clearing interrupt.
        await p.live._handle_evt_transcript_delta(SimpleNamespace(role="user", delta="Actually stop"))
        await p.live._handle_evt_transcript_delta(SimpleNamespace(role="assistant", delta="Working"))
        await p.live._close_open_turns()
        p.network.set()
        await until(lambda: len(p.writes) == len(expected) // 1920 and p.writes[-1][1].done())
        await until(lambda: len(p.packets) >= 52)  # Drain codec delay with production silence.
        assert b"".join(frame.to_ndarray().tobytes() for frame in p.consumed).startswith(expected)
        assert not any(isinstance(event, InterruptionFrame) for event in p.events)
        assert not p.errors
        # The real sender emits contiguous 20ms Opus packets at a 48kHz RTP clock.
        for previous, current in zip(p.packets, p.packets[1:]):
            assert (current.sequence_number - previous.sequence_number) % 65536 == 1
            assert (current.timestamp - previous.timestamp) % (2**32) == 960
        decoder = OpusDecoder()
        decoded = [frame.to_ndarray().reshape(-1, 2)[:, 0]
                   for packet in p.packets
                   for frame in decoder.decode(JitterFrame(packet.payload, packet.timestamp))]
        signal = np.concatenate(decoded)
        # Lossy Opus is not byte-identical. Every interior 20ms window must retain
        # its intended tone, including both sides of the blocked RTP write.
        for index, frequency in enumerate((440, 880, 1320)):
            for offset in range(index * 15360 + 1920, (index + 1) * 15360 - 1920, 960):
                window = signal[offset:offset + 960].astype(float)
                assert np.sqrt(np.mean(window**2)) > 3000
                peak = np.argmax(abs(np.fft.rfft(window * np.hanning(960)))) * 50
                assert abs(peak - frequency) <= 50


async def test_live_silence_cycles_do_not_discard_later_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    async with output_probe(monkeypatch) as p:
        expected = tone(440, 160) + bytes(24000 * 2) + tone(880, 160)
        await audio(p, expected)
        await until(lambda: bool(p.writes))
        await start_rtp(p)
        await until(lambda: len(p.writes) == len(expected) // 1920 and p.writes[-1][1].done())
        assert b"".join(f.to_ndarray().tobytes() for f in p.consumed).startswith(expected)
        speech = [type(e) for e in p.events if isinstance(e, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame))]
        assert speech[:3] == [BotStartedSpeakingFrame, BotStoppedSpeakingFrame, BotStartedSpeakingFrame]
        assert not any(isinstance(e, InterruptionFrame) for e in p.events)
        assert not p.errors


async def test_explicit_pipeline_interruption_discards_unsent_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    async with output_probe(monkeypatch) as p:
        await audio(p, tone(440, 400))
        await until(lambda: len(p.writes) == 1)
        await p.worker.queue_frame(InterruptionFrame())
        await until(lambda: any(isinstance(e, InterruptionFrame) for e in p.events))
        assert p.writes[0][1].cancelled()
        correction = tone(880, 160)
        await audio(p, correction)
        await until(lambda: len(p.writes) == 2)
        await start_rtp(p)
        await until(lambda: len(p.writes) == 5 and p.writes[-1][1].done())
        # Already-enqueued 40ms survives; remaining 360ms is intentionally lost.
        expected = tone(440, 40) + correction
        assert b"".join(f.to_ndarray().tobytes() for f in p.consumed).startswith(expected)
        assert not p.errors


async def test_stalled_rtp_consumer_reports_permanent_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async with output_probe(monkeypatch, timeout=0.1) as p:
        await audio(p, tone(440, 400))
        await until(lambda: bool(p.errors))
        assert "peer has stopped reading" in p.errors[0].error
        assert not p.output.is_usable
        assert len(p.writes) == 1
        assert p.writes[0][1].cancelled()
        assert p.consumed == []
