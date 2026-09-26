"""Response prefill ordering and cancellation, with exact PCM preservation."""
import asyncio
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")
from pipecat.frames.frames import (
    CancelFrame, EndFrame, InterruptionFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, OutputAudioRawFrame, TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from vauxr.realtime.output_buffer import OutputJitterBuffer


def pcm(ms=20):
    return OutputAudioRawFrame(audio=b"\x12\x34" * (24 * ms), sample_rate=24000, num_channels=1)


@pytest.fixture
async def probe(monkeypatch):
    # Transport/pipeline behavior is covered by test_realtime_live_output.
    monkeypatch.setattr(FrameProcessor, "process_frame", AsyncMock())
    monkeypatch.setattr(FrameProcessor, "cleanup", AsyncMock())
    buffer = OutputJitterBuffer(lead_ms=60)
    events = []
    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        events.append(frame)
    buffer.push_frame = push
    async def send(frame):
        await buffer.process_frame(frame, FrameDirection.DOWNSTREAM)
    yield buffer, events, send
    await buffer.cleanup()


async def test_prefill_preserves_frames_and_does_not_reprime_each_sentence(probe):
    buffer, events, send = probe
    frames = [pcm() for _ in range(3)]
    for frame in frames[:2]:
        await send(frame)
    assert not events
    await send(frames[2])
    assert events == frames
    stopped = TTSStoppedFrame()
    await send(stopped)
    next_sentence = pcm()
    await send(next_sentence)
    assert events == frames + [stopped, next_sentence]
    start = LLMFullResponseStartFrame()
    await send(start)
    await send(pcm())
    assert events[-1] is start


@pytest.mark.parametrize("marker_type", [TTSStoppedFrame, LLMFullResponseEndFrame, EndFrame])
async def test_short_reply_flushes_before_marker(probe, marker_type):
    buffer, events, send = probe
    frame, marker = pcm(), marker_type()
    await send(frame)
    await send(marker)
    await asyncio.sleep(.08)
    assert events == [frame, marker]


async def test_timeout_bounds_start_delay(probe):
    buffer, events, send = probe
    frame = pcm()
    await send(frame)
    assert not events
    async with asyncio.timeout(.5):
        while not events:
            await asyncio.sleep(.005)
    assert events == [frame]


@pytest.mark.parametrize("marker_type", [InterruptionFrame, CancelFrame])
async def test_interrupt_discards_prefill(probe, marker_type):
    buffer, events, send = probe
    await send(pcm())
    marker = marker_type()
    await send(marker)
    await asyncio.sleep(.08)
    assert events == [marker]
    if marker_type is InterruptionFrame:
        correction = pcm(60)
        await send(correction)
        assert events == [marker, correction]


async def test_normal_end_waits_for_in_progress_timed_flush(probe):
    buffer, events, send = probe
    entered, release = asyncio.Event(), asyncio.Event()
    frames = [pcm(), pcm()]
    async def slow_push(frame, direction=FrameDirection.DOWNSTREAM):
        if frame is frames[0]:
            entered.set()
            await release.wait()
        events.append(frame)
    buffer.push_frame = slow_push
    for frame in frames:
        await send(frame)
    await asyncio.wait_for(entered.wait(), .5)
    end = EndFrame()
    pending = asyncio.create_task(send(end))
    await asyncio.sleep(.01)
    assert not pending.done()
    release.set()
    await asyncio.wait_for(pending, .5)
    assert events == frames + [end]


async def test_interrupt_cancels_in_progress_timed_flush(probe):
    buffer, events, send = probe
    entered = asyncio.Event()
    old = pcm()
    async def slow_push(frame, direction=FrameDirection.DOWNSTREAM):
        if frame is old:
            entered.set()
            await asyncio.Event().wait()
        events.append(frame)
    buffer.push_frame = slow_push
    await send(old)
    await asyncio.wait_for(entered.wait(), .5)
    interrupt = InterruptionFrame()
    await asyncio.wait_for(send(interrupt), .5)
    correction = pcm(60)
    await send(correction)
    assert events == [interrupt, correction]


async def test_cleanup_drops_pending_audio(probe):
    buffer, events, send = probe
    await send(pcm())
    await buffer.cleanup()
    await asyncio.sleep(.08)
    assert not events
    assert buffer._timer is None
