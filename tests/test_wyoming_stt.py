"""Phase 7: Wyoming STT — parser + fake-server end-to-end."""

from __future__ import annotations

import asyncio
import json

import pytest

import config as cfg_mod
from wyoming_stt import (
    WyomingEvent,
    encode_event,
    parse_wyoming_events,
    transcribe,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    yield
    cfg_mod.reset_config()


# --- Pure parser tests ---

def test_parse_single_event_no_payload() -> None:
    buf = encode_event(WyomingEvent(type="transcript", data={"text": "hi"}))
    events, remainder = parse_wyoming_events(buf)
    assert len(events) == 1
    assert events[0].type == "transcript"
    assert events[0].data == {"text": "hi"}
    assert events[0].payload is None
    assert remainder == b""


def test_parse_event_with_payload() -> None:
    payload = b"\x01\x02\x03\x04"
    buf = encode_event(
        WyomingEvent(type="audio-chunk", data={"rate": 16000}, payload=payload)
    )
    events, remainder = parse_wyoming_events(buf)
    assert len(events) == 1
    assert events[0].payload == payload
    assert remainder == b""


def test_parse_multiple_events() -> None:
    e1 = encode_event(WyomingEvent(type="audio-start", data={"rate": 16000, "width": 2, "channels": 1}))
    e2 = encode_event(WyomingEvent(type="transcript", data={"text": "test"}))
    events, remainder = parse_wyoming_events(e1 + e2)
    assert [e.type for e in events] == ["audio-start", "transcript"]
    assert remainder == b""


def test_parse_partial_event_rewinds() -> None:
    full = encode_event(WyomingEvent(type="transcript", data={"text": "hi"}, payload=b"xxx"))
    partial = full[:-2]
    events, remainder = parse_wyoming_events(partial)
    assert events == []
    # We must rewind to the header start so the caller can append more.
    assert remainder == partial


def test_parse_partial_header_keeps_buffer() -> None:
    payload = b'{"type":"transcript","data":{"text":"hello"}}'  # no newline yet
    events, remainder = parse_wyoming_events(payload)
    assert events == []
    assert remainder == payload


def test_parse_data_length_separate_block() -> None:
    """Wyoming supports sending the data dict as a separate length-prefixed block."""
    extra = json.dumps({"text": "from-data-block"}).encode("utf-8")
    header = json.dumps({"type": "transcript", "data_length": len(extra)}).encode("utf-8")
    buf = header + b"\n" + extra
    events, remainder = parse_wyoming_events(buf)
    assert events[0].type == "transcript"
    assert events[0].data == {"text": "from-data-block"}
    assert remainder == b""


def test_malformed_header_skipped() -> None:
    bad = b"not json\n"
    good = encode_event(WyomingEvent(type="transcript", data={"text": "ok"}))
    events, _ = parse_wyoming_events(bad + good)
    assert len(events) == 1
    assert events[0].data == {"text": "ok"}


# --- Fake-server integration ---


async def _start_fake_server(handler):
    server = await asyncio.start_server(handler, host="127.0.0.1", port=0)
    sockname = server.sockets[0].getsockname()
    return server, sockname[1]


@pytest.mark.asyncio
async def test_transcribe_against_fake_server(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[WyomingEvent] = []

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buf = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf += chunk
            events, buf = parse_wyoming_events(buf)
            for ev in events:
                received.append(ev)
                if ev.type == "audio-stop":
                    writer.write(encode_event(WyomingEvent(type="transcript", data={"text": "hello world"})))
                    await writer.drain()
                    writer.close()
                    return

    server, port = await _start_fake_server(handler)
    try:
        cfg_mod.reset_config()
        monkeypatch.setenv("WHISPER_URL", f"tcp://127.0.0.1:{port}")
        cfg_mod.reset_config()

        pcm = b"\x00" * 3200  # 100ms @ 16kHz/16-bit/mono
        text = await transcribe([pcm, pcm])
        assert text == "hello world"

        assert received[0].type == "audio-start"
        assert received[0].data == {"rate": 16000, "width": 2, "channels": 1}
        assert received[1].type == "audio-chunk"
        assert received[1].payload is not None
        assert len(received[1].payload) == 3200
        assert received[2].type == "audio-chunk"
        assert received[3].type == "audio-stop"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_transcribe_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # Eat everything, never reply.
        while await reader.read(4096):
            pass
        writer.close()

    server, port = await _start_fake_server(handler)
    try:
        cfg_mod.reset_config()
        monkeypatch.setenv("WHISPER_URL", f"tcp://127.0.0.1:{port}")
        cfg_mod.reset_config()

        with pytest.raises(asyncio.TimeoutError):
            await transcribe([b"\x00" * 100], timeout=0.2)
    finally:
        server.close()
        await server.wait_closed()
