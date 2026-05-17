"""Phase 8: Wyoming TTS — fake-server end-to-end + resampler sanity."""

from __future__ import annotations

import asyncio
import struct

import pytest

from vauxr import config as cfg_mod
from vauxr.wyoming_stt import WyomingEvent, encode_event, parse_wyoming_events
from vauxr.wyoming_tts import _make_resampler, synthesize


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    yield
    cfg_mod.reset_config()


async def _start(handler):
    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    return server, server.sockets[0].getsockname()[1]


def _silence(samples: int) -> bytes:
    return b"\x00\x00" * samples


@pytest.mark.asyncio
async def test_synthesize_streams_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_text: list[str] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buf = b""
        while True:
            data = await reader.read(4096)
            if not data:
                return
            buf += data
            events, buf = parse_wyoming_events(buf)
            for ev in events:
                if ev.type == "synthesize":
                    requested_text.append(ev.data["text"])
                    writer.write(encode_event(WyomingEvent(type="audio-start", data={"rate": 22050})))
                    writer.write(
                        encode_event(WyomingEvent(type="audio-chunk", data={"rate": 22050}, payload=_silence(100)))
                    )
                    writer.write(
                        encode_event(WyomingEvent(type="audio-chunk", data={"rate": 22050}, payload=_silence(50)))
                    )
                    writer.write(encode_event(WyomingEvent(type="audio-stop", data={})))
                    await writer.drain()
                    writer.close()
                    return

    server, port = await _start(handler)
    try:
        monkeypatch.setenv("PIPER_URL", f"tcp://127.0.0.1:{port}")
        cfg_mod.reset_config()

        rates_seen: list[int] = []
        out_chunks: list[bytes] = []
        async for chunk in synthesize("hello", on_sample_rate=rates_seen.append):
            out_chunks.append(chunk)

        assert requested_text == ["hello"]
        # 2 chunks, unmodified (no target_rate set).
        assert [len(c) for c in out_chunks] == [200, 100]
        assert rates_seen == [22050]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_synthesize_resamples_when_target_rate_set(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buf = b""
        while True:
            data = await reader.read(4096)
            if not data:
                return
            buf += data
            events, buf = parse_wyoming_events(buf)
            for ev in events:
                if ev.type == "synthesize":
                    writer.write(encode_event(WyomingEvent(type="audio-start", data={"rate": 22050})))
                    writer.write(
                        encode_event(WyomingEvent(type="audio-chunk", data={"rate": 22050}, payload=_silence(220)))
                    )
                    writer.write(encode_event(WyomingEvent(type="audio-stop", data={})))
                    await writer.drain()
                    writer.close()
                    return

    server, port = await _start(handler)
    try:
        monkeypatch.setenv("PIPER_URL", f"tcp://127.0.0.1:{port}")
        cfg_mod.reset_config()

        rates_seen: list[int] = []
        out = b""
        async for chunk in synthesize("x", target_rate=16000, on_sample_rate=rates_seen.append):
            out += chunk

        # 220 samples at 22050 → ~160 samples at 16000.
        expected = round(220 * 16000 / 22050)
        assert len(out) // 2 == expected
        assert rates_seen == [16000]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_synthesize_abort_mid_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    abort = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buf = b""
        while True:
            data = await reader.read(4096)
            if not data:
                return
            buf += data
            events, buf = parse_wyoming_events(buf)
            for ev in events:
                if ev.type == "synthesize":
                    writer.write(encode_event(WyomingEvent(type="audio-start", data={"rate": 22050})))
                    for _ in range(10):
                        writer.write(
                            encode_event(WyomingEvent(type="audio-chunk", data={"rate": 22050}, payload=_silence(100)))
                        )
                        await writer.drain()
                        await asyncio.sleep(0.05)
                    writer.close()
                    return

    server, port = await _start(handler)
    try:
        monkeypatch.setenv("PIPER_URL", f"tcp://127.0.0.1:{port}")
        cfg_mod.reset_config()

        chunks: list[bytes] = []
        async for chunk in synthesize("x", abort_event=abort):
            chunks.append(chunk)
            if len(chunks) >= 2:
                abort.set()
        # We should have stopped early (well before 10 chunks).
        assert len(chunks) < 10
    finally:
        server.close()
        await server.wait_closed()


def test_resampler_zero_input() -> None:
    rs = _make_resampler(22050, 16000)
    assert rs(b"") == b""


def test_resampler_output_length_correct() -> None:
    rs = _make_resampler(22050, 16000)
    # Build a 220-sample input.
    pcm = b"".join(struct.pack("<h", v) for v in range(220))
    out = rs(pcm)
    assert len(out) // 2 == round(220 * 16000 / 22050)


def test_resampler_upsample_no_overflow() -> None:
    """Regression: cutoff must be below source Nyquist (see comment in wyoming-tts.ts)."""
    rs = _make_resampler(22050, 48000)
    pcm = b"".join(struct.pack("<h", v) for v in range(200))
    out = rs(pcm)
    # Decoded samples must all be within int16 — saturation would indicate
    # filter instability (the original Node bug).
    samples = struct.unpack(f"<{len(out)//2}h", out)
    assert all(-32768 <= s <= 32767 for s in samples)
    # The first few samples should not have saturated to -32768.
    assert min(samples[:10]) > -30000
