"""Ephemeral browser-only playback through the production synthesizer."""

from __future__ import annotations

import asyncio
import io
import json
import wave
from contextlib import aclosing
from dataclasses import replace

from aiohttp import web

from vauxr.auth import connections
from vauxr.auth.policy import Role
from vauxr.auth.service import authenticate, current
from vauxr.auth.service import get_store as auth_store
from vauxr.config_files import unique_object
from vauxr.devices import registry
from vauxr.speech.store import VoiceConflict, get_store
from vauxr.speech.wyoming_tts import synthesize

SENTENCE = "Hello! This is a preview of my voice."


async def preview(request: web.Request) -> web.Response:
    proof = request.headers.getall("X-Vauxr-Browser", [])
    principal = authenticate(proof[0] if len(proof) == 1 else "")
    if (
        principal is None
        or principal.role != Role.DEVICE
        or auth_store().lifecycle.get("bindings", {}).get(principal.subject, {}).get("kind") != "browser"
    ):
        raise web.HTTPForbidden()
    entry = registry.get(principal.subject)

    def connected() -> bool:
        return (
            current(principal)
            and entry is not None
            and registry.get(principal.subject) is entry
            and not getattr(entry.ws, "closed", True)
            and any(c.principal == principal and not c.retiring for c in connections._connections)
        )

    if not connected():
        return web.json_response({"error": "Connect this browser first"}, status=409)
    try:
        raw = bytearray()
        async with asyncio.timeout(5):
            async for chunk in request.content.iter_chunked(4097):
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError()
        if request.content_type != "application/json" or request.query_string:
            raise ValueError()
        body = json.loads(raw, object_pairs_hook=unique_object)
        if (
            not isinstance(body, dict)
            or set(body) != {"device_id", "tts_backend", "voice"}
            or body["device_id"] != principal.subject
            or not all(isinstance(value, str) for value in body.values())
        ):
            raise ValueError()
        store = get_store()
        selected = store.resolve(principal.subject)
        backend = store.backends.get(body["tts_backend"])
        if backend is None or backend.kind != "tts" or body["voice"] not in backend.voices:
            raise ValueError()
        selected = replace(selected, tts=backend, voice_id=body["voice"])
    except VoiceConflict:
        return web.json_response({"error": "Browser voice is disabled"}, status=409)
    except (ValueError, TypeError, TimeoutError):
        return web.json_response({"error": "Invalid browser voice selection"}, status=400)

    async def collect() -> bytes:
        pcm = bytearray()
        async with aclosing(synthesize(SENTENCE, selection=selected, target_rate=24000)) as stream:
            async for chunk in stream:
                pcm.extend(chunk)
                if len(pcm) > 24000 * 2 * 30:
                    raise ValueError("Preview too long")
        if not pcm or len(pcm) % 2:
            raise ValueError("No audio")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(pcm)
        return output.getvalue()

    task = asyncio.create_task(collect())
    try:
        async with asyncio.timeout(20):
            while not task.done():
                if not connected() or request.transport is None or request.transport.is_closing():
                    raise ConnectionError()
                await asyncio.wait({task}, timeout=0.05)
            audio = task.result()
        return web.Response(body=audio, content_type="audio/wav", headers={"Cache-Control": "no-store"})
    except (OSError, ValueError, TimeoutError, RuntimeError):
        return web.json_response({"error": "Voice preview unavailable; retry when connected"}, status=503)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
