"""Pipecat STT/TTS services backed by Wyoming (Whisper + Piper).

Canonical, in-tree version of the realtime PoC's `wyoming_services.py`. Endpoints
come from `config.get_config()` (the same Whisper/Piper the WS pipeline uses) so
the realtime path shares one configuration surface with the rest of vauxr.

Pipecat is an optional dependency (only needed when REALTIME_ENABLED=1); imports
here are top-level on purpose — this module must only be imported from the
realtime path, which is itself lazily imported.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from config import get_config


@dataclass
class _WyomingEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes | None = None


def _encode_event(event: _WyomingEvent) -> bytes:
    obj: dict[str, Any] = {"type": event.type, "data": event.data}
    if event.payload and len(event.payload) > 0:
        obj["payload_length"] = len(event.payload)
    line = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
    if event.payload and len(event.payload) > 0:
        return line + event.payload
    return line


def _parse_wyoming_events(buf: bytes) -> tuple[list[_WyomingEvent], bytes]:
    events: list[_WyomingEvent] = []
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
            continue
        if not isinstance(parsed, dict):
            continue

        data_len = int(parsed.get("data_length") or 0)
        payload_len = int(parsed.get("payload_length") or 0)
        total_trailing = data_len + payload_len
        if total_trailing > 0 and offset + total_trailing > n:
            offset = line_start
            break

        data: dict[str, Any] = parsed.get("data") or {}
        if data_len > 0:
            try:
                data = json.loads(buf[offset : offset + data_len].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            offset += data_len

        payload: bytes | None = None
        if payload_len > 0:
            payload = bytes(buf[offset : offset + payload_len])
            offset += payload_len

        events.append(_WyomingEvent(type=parsed.get("type", ""), data=data, payload=payload))

    return events, bytes(buf[offset:])


class WyomingSTTService(SegmentedSTTService):
    """Batch STT via Wyoming faster-whisper — one transcript per VAD segment."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        ep = get_config().whisper
        self._host, self._port = ep.host, ep.port

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if not audio:
            return

        # faster-whisper hallucinates on tiny noise blips. Drop segments shorter
        # than ~0.4s before they reach Whisper.
        min_bytes = int(0.4 * self.sample_rate * 2)
        if len(audio) < min_bytes:
            logger.debug("Wyoming STT: dropping {}-byte segment (< 0.4s)", len(audio))
            return

        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(
                _encode_event(
                    _WyomingEvent(
                        type="audio-start",
                        data={"rate": self.sample_rate, "width": 2, "channels": 1},
                    )
                )
            )
            writer.write(
                _encode_event(
                    _WyomingEvent(
                        type="audio-chunk",
                        data={"rate": self.sample_rate, "width": 2, "channels": 1},
                        payload=audio,
                    )
                )
            )
            writer.write(_encode_event(_WyomingEvent(type="audio-stop", data={})))
            await writer.drain()

            buf = b""
            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                buf += chunk
                events, buf = _parse_wyoming_events(buf)
                for ev in events:
                    if ev.type == "transcript":
                        text = ev.data.get("text", "")
                        if isinstance(text, str) and text.strip():
                            logger.info("Wyoming STT: {}", text.strip())
                            yield TranscriptionFrame(
                                text.strip(),
                                self._user_id,
                                time_now_iso8601(),
                                Language.EN,
                            )
                        return
        except Exception as e:  # noqa: BLE001
            logger.error("Wyoming STT error: {}", e)
            yield ErrorFrame(f"Wyoming STT error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass


class WyomingTTSService(TTSService):
    """Streaming TTS via Wyoming Piper."""

    def __init__(
        self,
        *,
        text_aggregation_mode: TextAggregationMode = TextAggregationMode.TOKEN,
        **kwargs,
    ) -> None:
        # Aggregation mode is chosen per device by the caller:
        # - TOKEN (default): synthesize each text frame as it arrives. Used with
        #   the upstream IdleSegmenter, which already emits one spoken segment per
        #   frame.
        # - SENTENCE: pipecat buffers tokens and cuts on sentence boundaries
        #   (NLTK). Used when sentence segmentation is enabled for the device.
        super().__init__(
            text_aggregation_mode=text_aggregation_mode,
            push_start_frame=True,
            push_stop_frames=True,
            **kwargs,
        )
        piper = get_config().piper
        self._host, self._port = piper.host, piper.port
        self._voice = piper.voice

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        logger.debug("Wyoming TTS: {}", text)
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(
                _encode_event(
                    _WyomingEvent(
                        type="synthesize",
                        data={"text": text, "voice": {"name": self._voice}},
                    )
                )
            )
            await writer.drain()

            buf = b""
            piper_rate = 0
            pcm_parts: list[bytes] = []

            while True:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                buf += chunk
                events, buf = _parse_wyoming_events(buf)
                stop = False
                for ev in events:
                    if ev.type == "audio-start" and isinstance(ev.data.get("rate"), (int, float)):
                        piper_rate = int(ev.data["rate"])
                    elif ev.type == "audio-chunk" and ev.payload:
                        if piper_rate == 0 and isinstance(ev.data.get("rate"), (int, float)):
                            piper_rate = int(ev.data["rate"])
                        pcm_parts.append(ev.payload)
                    elif ev.type == "audio-stop":
                        stop = True
                if stop:
                    break

            if pcm_parts:

                async def pcm_stream() -> AsyncIterator[bytes]:
                    for part in pcm_parts:
                        yield part

                async for frame in self._stream_audio_frames_from_iterator(
                    pcm_stream(),
                    in_sample_rate=piper_rate or 22050,
                    context_id=context_id,
                ):
                    yield frame
        except Exception as e:  # noqa: BLE001
            logger.error("Wyoming TTS error: {}", e)
            yield ErrorFrame(f"Wyoming TTS error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError):
                pass
