"""OpenAI Standard-mode TTS adapter against a local fake speech API; no paid calls."""

import asyncio
import struct

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import config
import openai_tts
import speech
from speech import Backend, SpeechStore
from speech_catalog import load_backends, validate_backend
from wyoming_tts import synthesize

PCM = struct.pack("<2400h", *([1000, -1000] * 1200))  # 100 ms at 24 kHz


@pytest.fixture
async def fake_openai():
    state = {"requests": [], "status": 200, "model_status": 200}

    async def speech_route(request):
        state["requests"].append({"auth": request.headers.get("Authorization"),
                                  "body": await request.json()})
        if state["status"] != 200:
            return web.json_response({"error": {"message": "bad"}}, status=state["status"])
        response = web.StreamResponse()
        await response.prepare(request)
        # Odd-sized chunks: adapter must realign to whole 16-bit samples.
        for start in range(0, len(PCM), 1001):
            await response.write(PCM[start:start + 1001])
        await response.write_eof()
        return response

    async def model_route(request):
        state["auth"] = request.headers.get("Authorization")
        return web.json_response({"id": request.match_info["model"]}, status=state["model_status"])

    app = web.Application()
    app.router.add_post("/v1/audio/speech", speech_route)
    app.router.add_get("/v1/models/{model}", model_route)
    server = TestServer(app)
    await server.start_server()
    backend = Backend("openai-tts", "tts", "openai", "gpt-4o-mini-tts", "127.0.0.1", server.port,
                      openai_tts.VOICES)
    validate_backend(backend)
    yield backend, state
    await server.close()


def _selection(backend, voice="marin"):
    return speech.Selection(Backend("whisper", "stt", "whisper", "m", "127.0.0.1", 1), backend, voice)


async def test_streams_pcm_with_shared_voice_and_resamples(fake_openai, monkeypatch):
    backend, state = fake_openai
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    rates = []
    out = b"".join([c async for c in synthesize("hello", selection=_selection(backend),
                                                 on_sample_rate=rates.append)])
    assert out == PCM and rates == [24000]
    assert state["requests"][0]["auth"] == "Bearer local-test-key"
    assert state["requests"][0]["body"] == {"model": "gpt-4o-mini-tts", "voice": "marin",
                                            "input": "hello", "response_format": "pcm"}

    rates.clear()
    out16 = b"".join([c async for c in synthesize("hello", selection=_selection(backend, "cedar"),
                                                   target_rate=16000, on_sample_rate=rates.append)])
    assert rates == [16000]
    assert abs(len(out16) - len(PCM) * 16000 // 24000) <= 4
    assert state["requests"][1]["body"]["voice"] == "cedar"


async def test_abort_stops_stream_and_errors_surface_without_body(fake_openai, monkeypatch):
    backend, state = fake_openai
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    abort = asyncio.Event()
    chunks = []
    async for chunk in synthesize("hello", selection=_selection(backend), abort_event=abort):
        chunks.append(chunk)
        abort.set()
    assert len(chunks) == 1

    state["status"] = 401
    with pytest.raises(openai_tts.OpenAITTSError) as error:
        async for _ in synthesize("hello", selection=_selection(backend)):
            pass
    assert "401" in str(error.value) and "bad" not in str(error.value)

    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(openai_tts.OpenAITTSError):
        async for _ in synthesize("hello", selection=_selection(backend)):
            pass
    assert len(state["requests"]) == 2  # no request without a credential


async def test_readiness_probe_is_bounded_and_credentialed(fake_openai, monkeypatch):
    backend, state = fake_openai
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert await speech.readiness(backend) == "unavailable"
    assert "auth" not in state
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    assert await speech.readiness(backend) == "ready"
    assert state["auth"] == "Bearer local-test-key"
    state["model_status"] = 404
    assert await speech.readiness(backend) == "unavailable"
    offline = Backend("openai-tts", "tts", "openai", "gpt-4o-mini-tts", "127.0.0.1", 9, openai_tts.VOICES)
    assert await speech.readiness(offline) == "unavailable"


def test_catalog_offers_openai_as_option_with_piper_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.reset_config()
    assert [b.id for b in load_backends(config.get_config()) if b.kind == "tts"] == ["piper"]

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-key")
    config.reset_config()
    backends = load_backends(config.get_config())
    openai = next(b for b in backends if b.id == "openai-tts")
    assert openai.adapter == "openai" and openai.voices[:2] == ("marin", "cedar")
    assert "api.openai.com" in openai_tts.base_url(openai) and openai_tts.base_url(openai).startswith("https")

    store = SpeechStore(tmp_path, backends)
    assert store.resolve().tts.id == "piper"
    store.update({"tts_backend": "openai-tts"}, "living-room")
    chosen = store.resolve("living-room")
    assert (chosen.tts.id, chosen.voice_id) == ("openai-tts", "marin")
    store.update({"voices": {"openai-tts": "cedar"}}, "living-room")
    assert store.resolve("living-room").voice_id == "cedar"
    with pytest.raises(ValueError):
        store.update({"voices": {"openai-tts": "not-a-voice"}}, "living-room")
    assert store.resolve().tts.id == "piper"
    view = store.view("living-room")
    listed = next(b for b in view["backends"] if b["id"] == "openai-tts")
    assert "host" not in listed and listed["voices"][0] == "marin"
    config.reset_config()
