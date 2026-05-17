"""Wyoming STT client.

Port of `src/wyoming-stt.ts`. Connects via TCP, sends `audio-start` /
`audio-chunk` / `audio-stop`, returns the first `transcript.text`.

The Wyoming protocol multiplexes JSON event headers and optional binary
payloads over a stream. Each event is a newline-delimited JSON header,
optionally followed by `data_length` bytes of separate JSON data and
`payload_length` bytes of raw binary.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from config import get_config

log = logging.getLogger("vauxr.wyoming_stt")


@dataclass
class WyomingEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes | None = None


def encode_event(event: WyomingEvent) -> bytes:
    obj: dict[str, Any] = {"type": event.type, "data": event.data}
    if event.payload and len(event.payload) > 0:
        obj["payload_length"] = len(event.payload)
    line = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
    if event.payload and len(event.payload) > 0:
        return line + event.payload
    return line


def parse_wyoming_events(buf: bytes) -> tuple[list[WyomingEvent], bytes]:
    """Streaming parser. Returns parsed events + leftover bytes."""
    events: list[WyomingEvent] = []
    offset = 0
    n = len(buf)

    while offset < n:
        nl = buf.find(b"\n", offset)
        if nl == -1:
            break

        line_start = offset
        line = buf[offset:nl]
        offset = nl + 1

        try:
            parsed = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Malformed header — skip to next line, matching Node behavior.
            continue

        if not isinstance(parsed, dict):
            continue

        data_len = int(parsed.get("data_length") or 0)
        payload_len = int(parsed.get("payload_length") or 0)
        total_trailing = data_len + payload_len

        if total_trailing > 0 and offset + total_trailing > n:
            # Need more bytes — rewind to the header start.
            offset = line_start
            break

        data: dict[str, Any] = parsed.get("data") or {}
        if data_len > 0:
            try:
                data = json.loads(buf[offset : offset + data_len].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # keep the header's `data` — same fallback as Node
                pass
            offset += data_len

        payload: bytes | None = None
        if payload_len > 0:
            payload = bytes(buf[offset : offset + payload_len])
            offset += payload_len

        events.append(WyomingEvent(type=parsed.get("type", ""), data=data, payload=payload))

    return events, bytes(buf[offset:])


async def transcribe(
    chunks: list[bytes],
    sample_rate: int = 16000,
    timeout: float = 30.0,
) -> str:
    """Send audio chunks to whisper, return the first transcript text."""
    cfg = get_config()
    host = cfg.whisper.host
    port = cfg.whisper.port

    reader, writer = await asyncio.open_connection(host, port)

    try:
        writer.write(
            encode_event(
                WyomingEvent(
                    type="audio-start",
                    data={"rate": sample_rate, "width": 2, "channels": 1},
                )
            )
        )
        for chunk in chunks:
            writer.write(
                encode_event(
                    WyomingEvent(
                        type="audio-chunk",
                        data={"rate": sample_rate, "width": 2, "channels": 1},
                        payload=chunk,
                    )
                )
            )
        writer.write(encode_event(WyomingEvent(type="audio-stop", data={})))
        await writer.drain()

        async def _read_transcript() -> str:
            buf = b""
            while True:
                data = await reader.read(8192)
                if not data:
                    raise RuntimeError("STT connection closed before transcript")
                buf += data
                events, buf = parse_wyoming_events(buf)
                for ev in events:
                    if ev.type == "transcript":
                        text = ev.data.get("text", "")
                        return text if isinstance(text, str) else ""

        return await asyncio.wait_for(_read_transcript(), timeout=timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass
