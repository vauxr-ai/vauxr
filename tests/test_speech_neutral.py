"""Opaque deployment IDs exercise shared resolution without legacy providers."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import config
import device_registry
import speech
from speech import Backend, SpeechStore
from wyoming_protocol import WyomingError, WyomingEvent, encode_event, parse_wyoming_events


@pytest.fixture
def neutral_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVICE_TOKEN", "neutral-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config.reset_config()
    device_registry.reset()
    store = SpeechStore(tmp_path, (
        Backend("recognizer-a", "stt", "wyoming", "model-a", "127.0.0.1", 10300),
        Backend("speaker-a", "tts", "wyoming", "model-b", "127.0.0.1", 10200, ("voice-a", "voice-b")),
        Backend("recognizer-b", "stt", "wyoming", "model-c", "127.0.0.1", 10301),
        Backend("speaker-b", "tts", "wyoming", "model-d", "127.0.0.1", 10201, ("voice-c",)),
    ))
    store.update({"stt_backend": "recognizer-b", "tts_backend": "speaker-b"}, "device")
    monkeypatch.setattr(speech, "get_store", lambda: store)
    yield store
    device_registry.reset()
    config.reset_config()


def test_neutral_initial_defaults_and_persisted_kind_validation(neutral_store):
    assert neutral_store.resolve().stt.id == "recognizer-a"
    assert neutral_store.resolve().tts.id == "speaker-a"
    restored = SpeechStore(neutral_store.path.parent, tuple(neutral_store.backends.values()))
    assert restored.resolve("device") == neutral_store.resolve("device")
    restored.devices["device"]["stt_backend"] = "speaker-a"
    assert restored.view("device")["effective"] is None


@pytest.mark.parametrize("kind", ["stt", "tts"])
async def test_wyoming_error_is_shared_and_does_not_wait_for_disconnect(neutral_store, kind):
    from wyoming_stt import transcribe
    from wyoming_tts import synthesize

    async def handler(reader, writer):
        try:
            buf = b""
            while True:
                data = await reader.read(8192)
                if not data:
                    return
                events, buf = parse_wyoming_events(buf + data)
                if any(e.type in ("audio-stop", "synthesize") for e in events):
                    break
            writer.write(encode_event(WyomingEvent("error", {"text": "private provider detail"})))
            await writer.drain()
            await reader.read()  # client must close without waiting for EOF/timeout
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        selection = neutral_store.resolve("device")
        selection = replace(selection, **{kind: replace(getattr(selection, kind), port=port)})
        with pytest.raises(WyomingError, match=f"{kind.upper()} provider returned a Wyoming error"):
            async with asyncio.timeout(1):
                if kind == "stt":
                    await transcribe([b"\0\0"], backend=selection.stt)
                else:
                    await anext(synthesize("hello", selection=selection))


@pytest.mark.parametrize("path", ["ws", "cold-fallback", "button-prompt", "button-announce", "announce"])
async def test_transport_paths_resolve_device_selection_once(neutral_store, monkeypatch, path):
    import button_dispatch
    import pipeline
    from realtime_session import RealtimeManager
    from server import AppState, ConnectionCtx, _voice_end, _voice_start

    expected = neutral_store.resolve("device")
    resolver = Mock(wraps=neutral_store.resolve)
    monkeypatch.setattr(neutral_store, "resolve", resolver)
    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), send_bytes=AsyncMock())
    channels = SimpleNamespace(get_active_channel=lambda: SimpleNamespace(type="openclaw-direct"))
    captured = []
    transcribed = []
    done = asyncio.Event()

    async def transcribe(chunks, *, backend):
        transcribed.append(backend)
        return "hello"

    async def synthesize(text, *, selection, **kwargs):
        captured.append(selection)
        # A settings edit between segments must not change this turn.
        neutral_store.update({"tts_backend": "speaker-a"}, "device")
        yield b"\0\0"

    async def route(device_id, text, ws, client, abort, rate, *, selection, send_audio_end):
        for segment in ("one", "two"):
            await pipeline._synthesize_and_send(ws, device_id, segment, abort, rate, selection)
        done.set()

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "synthesize", synthesize)
    monkeypatch.setattr(button_dispatch, "synthesize", synthesize)
    monkeypatch.setattr(pipeline, "_route_via_openclaw_direct", route)
    if path == "ws":
        state = AppState(openclaw_client=object(), channel_server=channels)
        ctx = ConnectionCtx(device_id="device")
        await _voice_start(state, ws, ctx, {"device_id": "device", "token": "neutral-test"})
        await _voice_end(state, ws, ctx)
        await asyncio.wait_for(done.wait(), 1)
    elif path == "cold-fallback":
        manager = RealtimeManager()
        manager.begin_preroll("device")
        manager.add_preroll("device", b"\0\0")
        await manager.handle_cold_voice_end(
            "device", webrtc_connected=False, ws=ws, openclaw_client=object(),
            channel_server=channels, output_sample_rate=None,
        )
        await asyncio.wait_for(done.wait(), 1)
    else:
        entry = device_registry.register("device", ws=ws)
        if path == "announce":
            assert await button_dispatch.announce_to_device(entry, "hello")
        else:
            device_registry.update_config("device", {"button_actions": {
                "double_press": {"kind": path.removeprefix("button-"), "text": "hello"},
            }})
            await button_dispatch.handle_device_button(
                device_id="device", button="action", gesture="double_press",
                openclaw_client=object(), channel_server=channels,
            )
    if path == "cold-fallback":
        # Sustained WS fallback arms the NEXT turn after completing this one.
        assert resolver.call_count == 2
        assert manager._speech_preroll["device"].tts.id == "speaker-a"
    else:
        resolver.assert_called_once_with("device")
    assert captured and all(s == expected for s in captured)
    assert transcribed == ([expected.stt] if path in ("ws", "cold-fallback") else [])


async def test_realtime_seed_and_segment_adapters_share_neutral_snapshot(neutral_store, monkeypatch):
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import Frame, TranscriptionFrame

    import realtime_wyoming
    import wyoming_stt
    from realtime_session import RealtimeSession

    session = RealtimeSession("device", None)
    session._pipeline_ready.set()
    monkeypatch.setattr(session, "_seed_user_text", AsyncMock())
    expected = neutral_store.resolve("device")
    captured = []

    async def transcribe(chunks, *, backend, **kwargs):
        assert backend == expected.stt
        return "hello"

    async def synthesize(text, *, selection, **kwargs):
        captured.append(selection)
        yield b"\0\0"

    async def frames(iterator, **kwargs):
        async for _ in iterator:
            yield Frame()

    monkeypatch.setattr(wyoming_stt, "transcribe", transcribe)
    monkeypatch.setattr(realtime_wyoming, "transcribe", transcribe)
    monkeypatch.setattr(realtime_wyoming, "synthesize", synthesize)
    await session.seed_buffered_turn(b"\0\0", expected)
    selected = lambda: session._speech_selection or speech.resolve("device")
    stt = realtime_wyoming.WyomingSTTService(selection=selected, sample_rate=16000)
    tts = realtime_wyoming.WyomingTTSService(selection=selected)
    monkeypatch.setattr(tts, "_stream_audio_frames_from_iterator", frames)
    await tts.on_turn_context_created("reply")
    neutral_store.update({"tts_backend": "speaker-a"}, "device")
    result = [f async for f in stt.run_stt(b"\0\0" * 16000)]
    assert len(result) == 1 and isinstance(result[0], TranscriptionFrame)
    for text in ("one", "two"):
        assert len([f async for f in tts.run_tts(text, "reply")]) == 1
    assert captured == [expected, expected]
    await session.seed_text_turn("next")
    assert session._speech_selection.tts.id == "speaker-a"
