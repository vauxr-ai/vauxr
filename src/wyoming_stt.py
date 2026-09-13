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
import logging

from speech import Backend, resolve
from wyoming_protocol import (
    WyomingError,
    WyomingEvent,
    encode_event,
    parse_wyoming_events,
)

log = logging.getLogger("vauxr.wyoming_stt")


async def transcribe(
    chunks: list[bytes],
    sample_rate: int = 16000,
    timeout: float = 30.0,
    *,
    backend: Backend | None = None,
) -> str:
    """Send audio chunks to the selected Wyoming STT service, return the first transcript text."""
    backend = backend or resolve().stt
    host, port = backend.host, backend.port

    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)

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
                    raise WyomingError("STT connection closed before transcript")
                buf += data
                events, buf = parse_wyoming_events(buf)
                for ev in events:
                    if ev.type == "error":
                        raise WyomingError("STT provider returned a Wyoming error")
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
