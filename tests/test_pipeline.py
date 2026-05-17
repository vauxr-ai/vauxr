"""Phase 11: full voice-turn pipeline."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from vauxr import (
    channel_registry,
    config as cfg_mod,
    device_registry as dev_reg,
    pipeline,
    wyoming_stt,
    wyoming_tts,
)
from channel_server import ChannelServer
from pipeline import resolve_follow_up, run_voice_turn


# --- Test doubles ---


class FakeWs:
    """Duck-typed stand-in for aiohttp.web.WebSocketResponse."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.binary: list[bytes] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.text.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(bytes(data))

    def json_messages(self) -> list[dict]:
        return [json.loads(t) for t in self.text]


@dataclass
class FakeChannel:
    id: str
    name: str
    type: str


class FakeOpenClawClient:
    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas
        self.calls: list[tuple[str, str]] = []
        self._on_chat_start = None

    def on_chat_start(self, cb):
        self._on_chat_start = cb
        return self

    async def chat(self, session_key: str, message: str, on_delta) -> None:
        self.calls.append((session_key, message))
        if self._on_chat_start is not None:
            await self._on_chat_start(on_delta)
            return
        for d in self.deltas:
            on_delta(d)


# --- Fixtures ---


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STREAMING_TTS_IDLE_PAUSE_MS", "20")
    # openclaw-direct requires OPENCLAW_URL to materialize as an active channel.
    monkeypatch.setenv("OPENCLAW_URL", "wss://test.invalid/")
    dev_reg.reset()
    channel_registry._reset_for_tests()
    yield
    channel_registry._reset_for_tests()
    dev_reg.reset()
    cfg_mod.reset_config()


def _set_direct_active():
    # Reach into the registry to flip openclaw-direct on without disk I/O.
    channel_registry._openclaw_direct_active = True  # type: ignore[attr-defined]


def _set_channel_active():
    ch = channel_registry.Channel(
        id="ch-1",
        name="My Channel",
        type="openclaw",
        tokenHash="hash",
        active=True,
        createdAt="2026-05-17T00:00:00Z",
    )
    channel_registry._set_active_for_tests(ch)


async def _fake_synth_yielding(text: str, **_k):
    yield b"fake-audio-data"


def _patch_stt(monkeypatch: pytest.MonkeyPatch, result: str | Exception):
    async def fake(*_a, **_k):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(wyoming_stt, "transcribe", fake)
    monkeypatch.setattr(pipeline, "transcribe", fake)


def _patch_tts(monkeypatch: pytest.MonkeyPatch, calls: list[str] | None = None):
    async def fake(text: str, **_k):
        if calls is not None:
            calls.append(text)
        yield b"fake-audio"

    monkeypatch.setattr(wyoming_tts, "synthesize", fake)
    monkeypatch.setattr(pipeline, "synthesize", fake)


# --- Tests ---


@pytest.mark.asyncio
async def test_runs_full_voice_turn_direct_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_direct_active()
    _patch_stt(monkeypatch, "what is the weather")
    _patch_tts(monkeypatch)

    ws = FakeWs()
    oc = FakeOpenClawClient(["The weather is nice today. It will be sunny tomorrow."])
    abort = asyncio.Event()

    # register device so next_seq works
    dev_reg.register("dev1", ws=ws)

    await run_voice_turn("dev1", [b"\x00" * 100], ws, oc, ChannelServer(), abort)

    msgs = ws.json_messages()
    assert any(m.get("type") == "transcript" and m.get("text") == "what is the weather" for m in msgs)
    assert any(m.get("type") == "audio.end" for m in msgs)
    # Binary 0x02 frames sent.
    assert ws.binary
    for frame in ws.binary:
        assert frame[0] == 0x02


@pytest.mark.asyncio
async def test_empty_transcript_sends_audio_end_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_direct_active()
    _patch_stt(monkeypatch, "")
    _patch_tts(monkeypatch)

    ws = FakeWs()
    oc = FakeOpenClawClient([])
    abort = asyncio.Event()

    await run_voice_turn("dev1", [b"\x00" * 100], ws, oc, ChannelServer(), abort)

    msgs = ws.json_messages()
    assert any(m.get("type") == "audio.end" for m in msgs)
    assert not any(m.get("type") == "transcript" for m in msgs)


@pytest.mark.asyncio
async def test_stt_error_emits_stt_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_direct_active()
    _patch_stt(monkeypatch, RuntimeError("whisper down"))

    ws = FakeWs()
    oc = FakeOpenClawClient([])
    abort = asyncio.Event()

    await run_voice_turn("dev1", [b"\x00" * 100], ws, oc, ChannelServer(), abort)

    msgs = ws.json_messages()
    err = next(m for m in msgs if m.get("type") == "error")
    assert err["code"] == "STT_ERROR"


@pytest.mark.asyncio
async def test_direct_mode_uses_last_delta_as_full_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_direct_active()
    _patch_stt(monkeypatch, "tell me a story")
    tts_calls: list[str] = []
    _patch_tts(monkeypatch, calls=tts_calls)

    async def deltas(on_delta):
        on_delta("Once upon a time there was a cat. ")
        on_delta("Once upon a time there was a cat. The cat sat on a warm cozy mat.")

    oc = FakeOpenClawClient([]).on_chat_start(deltas)

    ws = FakeWs()
    dev_reg.register("dev1", ws=ws)
    await run_voice_turn("dev1", [b"\x00" * 100], ws, oc, ChannelServer(), asyncio.Event())

    assert tts_calls == ["Once upon a time there was a cat. The cat sat on a warm cozy mat."]


@pytest.mark.asyncio
async def test_no_active_channel_emits_no_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stt(monkeypatch, "nobody is listening")
    _patch_tts(monkeypatch)

    ws = FakeWs()
    await run_voice_turn("dev1", [b"\x00" * 100], ws, None, ChannelServer(), asyncio.Event())

    msgs = ws.json_messages()
    assert any(m.get("type") == "error" and m.get("code") == "NO_CHANNEL" for m in msgs)
    assert any(m.get("type") == "audio.end" for m in msgs)


@pytest.mark.asyncio
async def test_abort_stops_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_direct_active()
    _patch_stt(monkeypatch, "hello world test phrase")
    _patch_tts(monkeypatch)

    abort = asyncio.Event()

    async def chat_then_abort(on_delta):
        abort.set()

    oc = FakeOpenClawClient([]).on_chat_start(chat_then_abort)

    ws = FakeWs()
    await run_voice_turn("dev1", [b"\x00" * 100], ws, oc, ChannelServer(), abort)

    msgs = ws.json_messages()
    assert not any(m.get("type") == "audio.end" for m in msgs)


@pytest.mark.asyncio
async def test_channel_mode_streaming_with_idle_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    """When deltas come with a gap > idle_pause, segment-flush triggers >1 TTS call."""
    _set_channel_active()
    _patch_stt(monkeypatch, "hello there")

    tts_calls: list[str] = []
    _patch_tts(monkeypatch, calls=tts_calls)

    ws = FakeWs()
    dev_reg.register("dev1", ws=ws)

    cs = ChannelServer()

    # Intercept send_transcript: arrange to feed deltas asynchronously.
    async def feed_deltas() -> None:
        # Wait until pipeline registers its listener.
        for _ in range(50):
            if cs.get_response_listener("dev1") is not None:
                break
            await asyncio.sleep(0.005)
        listener = cs.get_response_listener("dev1")
        assert listener is not None
        listener["on_delta"]("run-1", "I checked the calendar and ")
        await asyncio.sleep(0.005)
        listener["on_delta"]("run-1", "found something. ")
        # idle gap > 20ms triggers flush
        await asyncio.sleep(0.08)
        listener["on_delta"]("run-1", "You have a meeting at 3pm.")
        await asyncio.sleep(0.005)
        listener["on_end"]("run-1")

    def send_transcript_stub(device_id: str, text: str) -> bool:
        asyncio.create_task(feed_deltas())
        return True

    cs.send_transcript = send_transcript_stub  # type: ignore[method-assign]

    await run_voice_turn("dev1", [b"\x00" * 100], ws, None, cs, asyncio.Event())

    # Idle gap should have produced > 1 TTS call.
    assert len(tts_calls) > 1
    # Full text should reconstruct.
    assert "".join(tts_calls) == "I checked the calendar and found something. You have a meeting at 3pm."
    # audio.end exactly once.
    audio_ends = [m for m in ws.json_messages() if m.get("type") == "audio.end"]
    assert len(audio_ends) == 1


# --- resolve_follow_up ---


def test_resolve_always_strips_tag() -> None:
    r = resolve_follow_up("Hello [[follow_up]] there.", "always")
    assert r.follow_up is True
    assert r.reply_text == "Hello there."


def test_resolve_never_strips_tag() -> None:
    r = resolve_follow_up("Are you sure? [[follow_up]]", "never")
    assert r.follow_up is False
    assert r.reply_text == "Are you sure?"


def test_resolve_auto_with_tag() -> None:
    r = resolve_follow_up("Sure thing. [[follow_up]]", "auto")
    assert r.follow_up is True
    assert r.reply_text == "Sure thing."


def test_resolve_auto_with_question() -> None:
    r = resolve_follow_up("How are you?", "auto")
    assert r.follow_up is True
    assert r.reply_text == "How are you?"


def test_resolve_auto_with_fullwidth_question() -> None:
    r = resolve_follow_up("元気ですか？", "auto")
    assert r.follow_up is True
    assert r.reply_text == "元気ですか？"


def test_resolve_auto_statement() -> None:
    r = resolve_follow_up("All done.", "auto")
    assert r.follow_up is False
    assert r.reply_text == "All done."


def test_resolve_auto_tag_and_question_tag_wins_and_stripped() -> None:
    r = resolve_follow_up("Anything else? [[follow_up]]", "auto")
    assert r.follow_up is True
    assert r.reply_text == "Anything else?"


# --- audio.end follow_up flag in full pipeline ---


async def _run_with_mode(monkeypatch, mode, reply_text, transcript="hello"):
    _set_direct_active()
    _patch_stt(monkeypatch, transcript)
    _patch_tts(monkeypatch)
    dev_reg.update_config("dev1", {"follow_up_mode": mode})
    dev_reg.register("dev1", ws=object())
    ws = FakeWs()
    oc = FakeOpenClawClient([reply_text])
    await run_voice_turn("dev1", [b"\x00" * 100], ws, oc, ChannelServer(), asyncio.Event())
    return next(m for m in ws.json_messages() if m.get("type") == "audio.end")


@pytest.mark.asyncio
async def test_audio_end_always_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    end = await _run_with_mode(monkeypatch, "always", "All systems normal.")
    assert end == {"type": "audio.end", "follow_up": True}


@pytest.mark.asyncio
async def test_audio_end_never_mode_on_question(monkeypatch: pytest.MonkeyPatch) -> None:
    end = await _run_with_mode(monkeypatch, "never", "Want me to keep going?")
    assert end == {"type": "audio.end", "follow_up": False}


@pytest.mark.asyncio
async def test_audio_end_empty_transcript_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_direct_active()
    _patch_stt(monkeypatch, "")
    _patch_tts(monkeypatch)
    ws = FakeWs()
    await run_voice_turn("dev1", [b"\x00" * 100], ws, FakeOpenClawClient([]), ChannelServer(), asyncio.Event())
    end = next(m for m in ws.json_messages() if m.get("type") == "audio.end")
    assert end.get("follow_up") is False


@pytest.mark.asyncio
async def test_audio_end_no_active_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stt(monkeypatch, "hello")
    ws = FakeWs()
    await run_voice_turn("dev1", [b"\x00" * 100], ws, None, ChannelServer(), asyncio.Event())
    end = next(m for m in ws.json_messages() if m.get("type") == "audio.end")
    assert end.get("follow_up") is False
