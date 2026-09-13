"""Speech contracts, using local fake Wyoming peers; no models or GPUs."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

import config
import speech
from speech import Backend, SpeechStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_TOKEN", "speech-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config.reset_config()
    backends = (
        Backend("whisper", "stt", "whisper", "small", "127.0.0.1", 10300),
        Backend("parakeet", "stt", "parakeet-v3", "v3", "127.0.0.1", 10301),
        Backend("piper", "tts", "piper", "piper-model", "127.0.0.1", 10200, ("amy", "sam")),
        Backend("kokoro", "tts", "kokoro", "kokoro-model", "127.0.0.1", 10201, ("af", "bf")),
    )
    store = SpeechStore(tmp_path, backends)
    monkeypatch.setattr(speech, "get_store", lambda: store)
    import speech_http

    monkeypatch.setattr(speech_http, "get_store", lambda: store)
    yield store
    config.reset_config()


def test_dynamic_inheritance_model_voices_restart_and_reset(store):
    store.update({"tts_backend": "piper", "voices": {"piper": "sam"}}, "fixed")
    old = store.resolve("new")
    store.update({"stt_backend": "parakeet", "tts_backend": "kokoro", "voices": {"kokoro": "bf"}})
    assert store.resolve("new").voice_id == "bf"
    assert store.resolve("never-seen").tts.id == "kokoro"
    assert store.resolve("fixed").voice_id == "sam"
    assert store.resolve("fixed").stt.id == "parakeet"
    assert old.tts.id == "piper" and old.voice_id == "amy"
    restored = SpeechStore(store.path.parent, tuple(store.backends.values()))
    assert restored.resolve("fixed") == store.resolve("fixed")
    assert restored.resolve("new") == store.resolve("new")
    restored.update({"tts_backend": None, "voices": None}, "fixed")
    assert restored.resolve("fixed").voice_id == "bf"
    restored.update({"tts_backend": "piper"})
    assert restored.resolve("fixed").voice_id == "amy"


@pytest.mark.parametrize(
    "patch",
    [
        {"voice": "sam"},
        {"endpoint": "http://attacker"},
        {"tts_backend": "whisper"},
        {"tts_backend": []},
        {"voices": {"piper": "af"}},
        {"voices": {"whisper": "sam"}},
        {"stt_backend": None},
        [],
        {"voices": {"piper": None}},
    ],
)
def test_invalid_updates_are_atomic(store, patch):
    before = store.resolve()
    with pytest.raises(ValueError):
        store.update(patch)
    assert store.resolve() == before
    assert not store.path.exists()


def test_removed_provider_is_explicit_not_fallback(store):
    store.update({"tts_backend": "kokoro"}, "a")
    restored = SpeechStore(store.path.parent, tuple(b for b in store.backends.values() if b.id != "kokoro"))
    assert restored.view("a")["effective"] is None
    assert restored.view("a")["error"]
    with pytest.raises(KeyError):
        restored.resolve("a")
    restored.update({"tts_backend": None}, "a")
    assert restored.resolve("a").tts.id == "piper"


async def test_http_management_auth_validation_and_isolation(store, monkeypatch):
    import channel_registry
    import speech_http
    from http_server import make_http_app

    channel_registry._reset_for_tests()
    monkeypatch.setattr(speech_http, "readiness", AsyncMock(return_value="unavailable"))
    async with TestClient(TestServer(make_http_app())) as client:
        for path in ("/api/speech", "/api/devices/a/speech"):
            for method in ("GET", "PATCH"):
                r = await client.request(method, path, json={"tts_backend": "kokoro"})
                assert r.status == 401
        headers = {"Authorization": "Bearer speech-test"}
        r = await client.patch("/api/devices/a/speech", headers=headers, json={"tts_backend": "kokoro"})
        assert r.status == 200
        body = await r.json()
        assert body["effective"]["tts_backend"] == "kokoro"
        assert body["backends"][0]["readiness"] == "unavailable"
        assert all("host" not in b and "port" not in b for b in body["backends"])
        r = await client.get("/api/devices/b/speech", headers=headers)
        assert (await r.json())["effective"]["tts_backend"] == "piper"
        r = await client.patch("/api/speech", headers=headers, json={"url": "tcp://bad:1"})
        assert r.status == 400
        # Existing channel-token management boundary is preserved, not reimplemented.
        monkeypatch.setattr(channel_registry, "validate_channel_token", AsyncMock(return_value=object()))
        r = await client.get("/api/speech", headers={"Authorization": "Bearer channel-test"})
        assert r.status == 200


async def test_midturn_stt_to_multiple_tts_segments(store, monkeypatch):
    import device_registry
    import pipeline
    from server import AppState, ConnectionCtx, _voice_end, _voice_start

    selections = []
    stt_backends = []
    finished = asyncio.Event()

    async def transcribe(chunks, *, backend):
        stt_backends.append(backend.id)
        return "hello"

    async def synthesize(text, *, selection, **kwargs):
        selections.append(selection)
        store.update({"tts_backend": "kokoro", "voices": {"kokoro": "bf"}})
        yield b"\x00\x00"

    async def route(device_id, text, ws, client, abort, rate, *, selection, send_audio_end):
        await pipeline._synthesize_and_send(ws, device_id, "one", abort, rate, selection)
        await pipeline._synthesize_and_send(ws, device_id, "two", abort, rate, selection)
        finished.set()

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "synthesize", synthesize)
    monkeypatch.setattr(pipeline, "_route_via_openclaw_direct", route)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), send_bytes=AsyncMock())
    channels = SimpleNamespace(get_active_channel=lambda: SimpleNamespace(type="openclaw-direct"))
    state = AppState(openclaw_client=object(), channel_server=channels)
    ctx = ConnectionCtx()
    await _voice_start(
        state, ws, ctx, {"device_id": "a", "token": "speech-test", "tts_backend": "kokoro", "voice": "bf"}
    )
    store.update({"stt_backend": "parakeet", "voices": {"piper": "sam"}})
    await _voice_end(state, ws, ctx)
    await asyncio.wait_for(finished.wait(), 1)
    assert stt_backends == ["whisper"]
    assert [s.voice_id for s in selections] == ["amy", "amy"]
    assert store.resolve("a").voice_id == "bf"
    device_registry.reset()


async def test_announcements_snapshot_and_device_isolation(store, monkeypatch):
    import button_dispatch

    store.update({"tts_backend": "kokoro"}, "a")
    selected = []

    async def synthesize(text, *, selection, **kwargs):
        selected.append(selection)
        store.update({"voices": {"kokoro": "bf"}})
        yield b"\0\0"

    monkeypatch.setattr(button_dispatch, "synthesize", synthesize)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), send_bytes=AsyncMock())
    for device in ("a", "b"):
        await button_dispatch.announce_to_device(
            SimpleNamespace(id=device, ws=ws, output_sample_rate=None), "hi"
        )
    assert [(s.tts.id, s.voice_id) for s in selected] == [("kokoro", "af"), ("piper", "amy")]


async def test_cold_realtime_fallback_uses_wake_snapshot(store, monkeypatch):
    import pipeline
    from realtime_session import RealtimeManager

    manager = RealtimeManager()
    manager.begin_preroll("a")
    manager.add_preroll("a", b"\0\0")
    store.update({"stt_backend": "parakeet", "tts_backend": "kokoro"})
    run = AsyncMock()
    monkeypatch.setattr(pipeline, "run_voice_turn", run)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    await manager.handle_cold_voice_end(
        "a", webrtc_connected=False, ws=ws, openclaw_client=None, channel_server=None, output_sample_rate=None
    )
    selection = run.call_args.kwargs["selection"]
    assert selection.stt.id == "whisper" and selection.tts.id == "piper"


@pytest.mark.parametrize("generic", [False, True])
async def test_wyoming_adapter_wire_voice_and_readiness(store, generic):
    from wyoming_stt import WyomingEvent, encode_event, parse_wyoming_events, transcribe
    from wyoming_tts import synthesize

    requests = []

    async def handler(reader, writer):
        try:
            data = await reader.readline()
            request = json.loads(data)
            requests.append(request)
            if request["type"] == "describe":
                writer.write(encode_event(WyomingEvent("info", {"tts": [{}]})))
            elif request["type"] == "synthesize":
                writer.write(encode_event(WyomingEvent("audio-start", {"rate": 24000})))
                writer.write(encode_event(WyomingEvent("audio-chunk", {}, b"\0\0")))
                writer.write(encode_event(WyomingEvent("audio-stop")))
            else:
                buf = data
                while True:
                    events, buf = parse_wyoming_events(buf)
                    if any(e.type == "audio-stop" for e in events):
                        break
                    buf += await reader.read(8192)
                writer.write(encode_event(WyomingEvent("transcript", {"text": "test"})))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        selection = store.resolve()
        tts = replace(store.backends["kokoro"], port=port)
        stt = replace(store.backends["parakeet"], port=port)
        if generic:
            tts = replace(tts, id="speaker", adapter="wyoming", model="operator-model")
            stt = replace(stt, id="recognizer", adapter="wyoming", model="operator-model")
        selection = replace(selection, tts=tts, voice_id="bf")
        assert await speech.readiness(selection.tts) == "ready"
        assert [b async for b in synthesize("hello", selection=selection)] == [b"\0\0"]
        assert requests[-1]["data"]["voice"] == {"name": "bf"}
        assert await transcribe([b"\0\0"], backend=stt) == "test"
    assert await speech.readiness(selection.tts) == "unavailable"
    with pytest.raises(OSError):
        await anext(synthesize("hello", selection=selection))


async def test_cold_realtime_seed_preserves_complete_selection(store, monkeypatch):
    import wyoming_stt
    from realtime_session import RealtimeSession

    session = RealtimeSession("a", None)
    session._pipeline_ready.set()
    monkeypatch.setattr(session, "is_peer_live", lambda: True)
    monkeypatch.setattr(session, "_seed_user_text", AsyncMock())
    before = store.resolve("a")

    async def stt(chunks, *, backend):
        assert backend == before.stt
        store.update({"tts_backend": "kokoro"})
        return "hello"

    monkeypatch.setattr(wyoming_stt, "transcribe", stt)
    await session.seed_buffered_turn(b"\0\0", before)
    assert session._speech_selection == before
    session._seed_user_text.assert_awaited_once_with("hello")


async def test_realtime_adapter_reply_snapshot(store, monkeypatch):
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import Frame

    import realtime_wyoming

    service = realtime_wyoming.WyomingTTSService(selection=lambda: store.resolve("a"))
    captured = []

    async def synthesize(text, *, selection, **kwargs):
        captured.append(selection)
        yield b"\0\0"

    async def frames(iterator, **kwargs):
        async for _ in iterator:
            yield Frame()

    monkeypatch.setattr(realtime_wyoming, "synthesize", synthesize)
    monkeypatch.setattr(service, "_stream_audio_frames_from_iterator", frames)
    await service.on_turn_context_created("reply-1")
    for segment in ("first", "second"):
        store.update({"tts_backend": "kokoro"})
        assert len([f async for f in service.run_tts(segment, "reply-1")]) == 1
    assert [s.tts.id for s in captured] == ["piper", "piper"]
    await service.on_turn_context_created("reply-2")
    assert len([f async for f in service.run_tts("late old segment", "reply-1")]) == 1
    assert captured[-1].tts.id == "piper"
    assert len([f async for f in service.run_tts("next", "reply-2")]) == 1
    assert captured[-1].tts.id == "kokoro"
    from pipecat.frames.frames import InterruptionFrame
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.services.tts_service import TTSService

    monkeypatch.setattr(TTSService, "process_frame", AsyncMock())
    await service.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    assert service._selections == {}


async def test_announcement_outage_returns_503_and_error_frame(store, monkeypatch):
    import button_dispatch
    import device_registry
    from http_server import make_http_app

    async def failed(text, **kwargs):
        raise ConnectionRefusedError()
        yield b""  # async generator contract

    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), send_bytes=AsyncMock())
    device_registry.register("a", ws=ws)
    monkeypatch.setattr(button_dispatch, "synthesize", failed)
    async with TestClient(TestServer(make_http_app())) as client:
        response = await client.post(
            "/api/devices/a/announce", json={"text": "hello"}, headers={"Authorization": "Bearer speech-test"}
        )
        assert response.status == 503
    messages = [json.loads(call.args[0]) for call in ws.send_str.call_args_list]
    assert any(m.get("code") == "TTS_ERROR" for m in messages)
    assert messages[-1]["type"] == "audio.end"
    device_registry.reset()


def test_legacy_environment_and_operator_registry_restart(tmp_path, monkeypatch):
    from dataclasses import asdict

    monkeypatch.setenv("DEVICE_TOKEN", "speech-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WHISPER_URL", "tcp://127.0.0.1:12001")
    monkeypatch.setenv("PIPER_URL", "tcp://127.0.0.1:12002")
    monkeypatch.setenv("PIPER_VOICE", "legacy-voice")
    config.reset_config()
    try:
        extra = Backend("extra", "tts", "kokoro", "model", "127.0.0.1", 12003, ("af",))
        (tmp_path / "speech-providers.json").write_text(json.dumps([asdict(extra)]))
        initial = speech.resolve()
        assert initial.stt.port == 12001
        assert initial.tts.port == 12002 and initial.voice_id == "legacy-voice"
        speech.get_store().update({"tts_backend": "extra"})
        config.reset_config()
        assert speech.resolve().tts == extra
    finally:
        config.reset_config()


def test_neutral_env_migration_preserves_persisted_legacy_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_TOKEN", "speech-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    values = {
        "STT_URL": ("WHISPER_URL", "127.0.0.1:13001"),
        "TTS_URL": ("PIPER_URL", "127.0.0.1:13002"),
        "TTS_VOICE": ("PIPER_VOICE", "configured-voice"),
    }
    for name, (legacy, value) in values.items():
        monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(legacy, value)
    config.reset_config()
    try:
        store = speech.get_store()
        store.update({"tts_backend": "piper", "voices": {"piper": "configured-voice"}}, "a")
        before = store.resolve("a")
        persisted = store.path.read_bytes()
        for name, (legacy, value) in values.items():
            monkeypatch.setenv(name, value)
            monkeypatch.delenv(legacy)
        config.reset_config()
        assert speech.resolve("a") == before
        assert speech.get_store().path.read_bytes() == persisted
    finally:
        config.reset_config()
