"""Exercise startup replay against a provider clock and continuously arriving RTP."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import InputAudioRawFrame

import vauxr.realtime.startup as module
from vauxr.realtime.startup import StartupAudio


@pytest.mark.parametrize("rate", [16000, 24000])
async def test_long_startup_respects_provider_rate_and_catches_live_rtp(
    monkeypatch: pytest.MonkeyPatch, rate: int,
) -> None:
    startup = StartupAudio(1, AsyncMock(), input_rate=rate)
    now = 0.0
    received = 0.0
    next_rtp = 0.02
    incoming: list[bytes] = []
    delivered: list[bytes] = []

    async def sleep(delay: float) -> None:
        nonlocal now, next_rtp
        assert delay >= 0
        now += delay
        while next_rtp <= now:
            audio = (len(incoming) % 30000 + 1).to_bytes(2, "little") * (rate // 50)
            frame = InputAudioRawFrame(audio, rate, 1)
            assert startup.append_rtp(frame)
            incoming.append(audio)
            next_rtp += 0.02
        await asyncio.sleep(0)

    async def consume(frame: InputAudioRawFrame) -> None:
        nonlocal received
        received += len(frame.audio) / (frame.sample_rate * frame.num_channels * 2)
        # Simulate the provider's advertised acceptance envelope, with a
        # stricter margin than its five-second / 1.2x limit.
        assert received <= 4.0 + 1.15 * now + 1e-7
        delivered.append(frame.audio)

    monkeypatch.setattr(module, "monotonic", lambda: now)
    monkeypatch.setattr(module, "sleep", sleep)
    startup.append_ws(0, bytes(15 * 16000 * 2))
    setup_deadline = startup._timer.when()
    startup.bind(consume)
    startup.ready()
    assert startup.finish_ws(1, 1)
    assert startup._timer.when() > setup_deadline + 90
    await startup._drain_task
    assert startup.drained and not startup.closed
    assert 60 < now < startup.DRAIN_TIMEOUT_SECONDS
    assert abs(received - 15 - len(incoming) * 0.02) < 1e-7
    assert delivered[750:] == incoming
    assert not startup._ws and not startup._rtp and startup._bytes_24k == 0
    assert not startup.append_rtp(InputAudioRawFrame(bytes(rate // 50 * 2), rate, 1))
    startup.close()


async def test_slow_consumer_cannot_accumulate_unlimited_burst_credit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup = StartupAudio(1, AsyncMock(), input_rate=16000)
    now = 0.0
    after_stall = 0.0
    count = 0

    async def sleep(delay: float) -> None:
        nonlocal now
        now += delay

    async def consume(frame: InputAudioRawFrame) -> None:
        nonlocal now, count, after_stall
        count += 1
        if count == 1:
            now += 30
        else:
            after_stall += len(frame.audio) / (frame.sample_rate * 2)
            assert after_stall <= 4.0 + 1.15 * (now - 30) + 1e-7

    monkeypatch.setattr(module, "monotonic", lambda: now)
    monkeypatch.setattr(module, "sleep", sleep)
    startup.append_ws(0, bytes(8 * 16000 * 2))
    startup.bind(consume)
    startup.ready()
    startup.finish_ws(1, 1)
    await startup._drain_task
    assert now > 33 and count == 400 and startup.drained
    startup.close()


async def test_cancel_during_rate_limit_sleep_never_sends_stale_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = AsyncMock()
    startup = StartupAudio(1, failed, input_rate=16000)
    entered = asyncio.Event()
    blocked = asyncio.Event()
    consume = AsyncMock()

    async def sleep(delay: float) -> None:
        entered.set()
        await blocked.wait()

    monkeypatch.setattr(module, "sleep", sleep)
    startup.append_ws(0, bytes(6 * 16000 * 2))
    startup.bind(consume)
    startup.ready()
    startup.finish_ws(1, 1)
    await entered.wait()
    before = consume.await_count
    assert 190 <= before <= 200
    startup.close()
    await asyncio.gather(startup._drain_task, return_exceptions=True)
    assert consume.await_count == before
    assert startup.closed and not startup._ws and not startup._rtp
    failed.assert_not_called()
