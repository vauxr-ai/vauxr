"""OpenAI speech API adapter for Standard-mode TTS.

Streams 24 kHz 16-bit mono PCM from ``/v1/audio/speech`` so a device can use
the same ``marin``/``cedar`` voice in Standard mode and GPT-Live realtime mode.
The credential stays in the request header; it is never logged or returned.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable

import aiohttp

from speech_models import Backend, Selection

log = logging.getLogger("vauxr.openai_tts")

ADAPTER = "openai"
MODEL = "gpt-4o-mini-tts"
SOURCE_RATE = 24000
# Shared realtime voices first so the Standard default matches GPT-Live.
VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "fable",
          "nova", "onyx", "sage", "shimmer", "verse")


class OpenAITTSError(Exception):
    """Provider rejected or aborted synthesis; message never includes the body."""


def base_url(backend: Backend) -> str:
    scheme = "https" if backend.port == 443 else "http"
    return f"{scheme}://{backend.host}:{backend.port}"


def _headers() -> dict[str, str]:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise OpenAITTSError("OPENAI_API_KEY is not configured")
    return {"Authorization": f"Bearer {key}"}


async def readiness(backend: Backend) -> str:
    """Bounded model lookup; no synthesis, no audio, no credential exposure."""
    if not os.environ.get("OPENAI_API_KEY"):
        return "unavailable"
    try:
        async with asyncio.timeout(2), aiohttp.ClientSession(headers=_headers()) as http:
            async with http.get(f"{base_url(backend)}/v1/models/{backend.model}") as response:
                return "ready" if response.status == 200 else "unavailable"
    except (OSError, TimeoutError, aiohttp.ClientError, OpenAITTSError):
        return "unavailable"


async def synthesize(
    text: str,
    *,
    selection: Selection,
    target_rate: int | None = None,
    abort_event: asyncio.Event | None = None,
    on_sample_rate: Callable[[int], None] | None = None,
) -> AsyncIterator[bytes]:
    from wyoming_tts import _make_resampler

    backend = selection.tts
    body = {"model": backend.model, "voice": selection.voice_id, "input": text,
            "response_format": "pcm"}
    effective = target_rate if target_rate and target_rate != SOURCE_RATE else SOURCE_RATE
    resample = _make_resampler(SOURCE_RATE, effective) if effective != SOURCE_RATE else None
    timeout = aiohttp.ClientTimeout(sock_connect=10, sock_read=30)
    async with aiohttp.ClientSession(headers=_headers(), timeout=timeout) as http:
        async with http.post(f"{base_url(backend)}/v1/audio/speech", json=body) as response:
            if response.status != 200:
                raise OpenAITTSError(f"OpenAI speech request failed with status {response.status}")
            fired = False
            carry = b""
            async for chunk in response.content.iter_chunked(4096):
                if abort_event is not None and abort_event.is_set():
                    return
                data = carry + chunk
                if len(data) % 2:
                    data, carry = data[:-1], data[-1:]
                else:
                    carry = b""
                if not data:
                    continue
                if not fired and on_sample_rate is not None:
                    on_sample_rate(effective)
                    fired = True
                yield resample(data) if resample else data
