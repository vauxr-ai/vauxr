"""Regressions for the three PR #39 follow-up review findings."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import button_dispatch
import config
import device_registry as registry
import realtime_session
from channel_server import ChannelServer
from realtime_session import RealtimeSession


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DEVICE_TOKEN", "test")
    config.reset_config()
    registry.reset()
    monkeypatch.setattr(realtime_session, "_manager", realtime_session.RealtimeManager())
    yield
    registry.reset()
    config.reset_config()


async def test_empty_completion_behind_interrupted_audio_end() -> None:
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("review", ws=ws)
    session = RealtimeSession("review", channel_server=object())
    realtime_session.get_manager()._sessions[session.device_id] = session
    session._on_bot_started_speaking()
    await session._on_turn_complete(False, "Interrupted answer.")
    session._on_interruption()
    session._awaiting_reply = True
    session._turn_active = True
    await session._on_turn_complete(False, "")
    # The empty/error turn has no bot-start event to release PROCESSING.
    assert not session._awaiting_reply
    session._bot_speaking = 0
    session._bot_stop_credits = 1
    await session._drain_ends()
    assert not session._turns_suppressed()
    assert [json.loads(call.args[0])["follow_up"] for call in ws.send_str.call_args_list] == [True, False]
    assert registry.get("review").state == "idle"


async def test_delivered_quiet_end_with_intervening_explicit_pause() -> None:
    session = RealtimeSession("review", channel_server=object())
    realtime_session.get_manager()._sessions[session.device_id] = session

    async def send(_data: str) -> None:
        session.set_mic_paused(True)

    registry.register("review", ws=SimpleNamespace(closed=False, send_str=send))
    registry.set_state("review", "listening")
    await session._send_audio_end(False)
    assert session._mic_paused
    assert registry.get("review").state == "idle"


@pytest.mark.parametrize("follow_up", [False, True])
async def test_warm_ws_prompt_completion_matches_mic_and_registry(
    monkeypatch: pytest.MonkeyPatch, follow_up: bool,
) -> None:
    import pipeline

    ws = SimpleNamespace(closed=False, send_str=AsyncMock(), send_bytes=AsyncMock())
    registry.register("review", ws=ws)
    session = RealtimeSession("review", channel_server=object())
    session.set_mic_paused(True)
    manager = realtime_session.RealtimeManager()
    manager._sessions["review"] = session
    monkeypatch.setattr(realtime_session, "get_manager", lambda: manager)
    channel = ChannelServer()
    monkeypatch.setattr(channel, "get_active_channel", lambda: SimpleNamespace(type="openclaw-direct"))

    async def chat(_key: str, _text: str, on_delta: Callable[[str], None]) -> None:
        on_delta("Another question?" if follow_up else "Done.")

    async def synthesize(_text: str, **_kwargs: object) -> AsyncIterator[bytes]:
        yield b"\x01\x00" * 320

    monkeypatch.setattr(pipeline, "synthesize", synthesize)
    await button_dispatch._dispatch_prompt("review", "Hello", SimpleNamespace(chat=chat), channel)
    ends = [json.loads(call.args[0]) for call in ws.send_str.call_args_list
            if json.loads(call.args[0])["type"] == "audio.end"]
    assert ends == [{"type": "audio.end", "follow_up": follow_up}]
    assert session._mic_paused is (not follow_up)
    assert registry.get("review").state == ("listening" if follow_up else "idle")
    ws.send_bytes.assert_awaited()


@pytest.mark.parametrize("replacement", ["turn", "connection"])
async def test_old_prompt_does_not_complete_or_clear_new_owner(
    monkeypatch: pytest.MonkeyPatch, replacement: str,
) -> None:
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    registry.register("review", ws=ws)
    new_abort = asyncio.Event()
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "get_manager", lambda: manager)
    completion = AsyncMock()
    monkeypatch.setattr(manager, "send_prompt_audio_end", completion)

    async def run(
        *_args: object, send_audio_end: Callable[[bool], Awaitable[None]],
    ) -> None:
        if replacement == "connection":
            registry.unregister("review")
            registry.register("review", ws=SimpleNamespace(closed=False, send_str=AsyncMock()))
        entry = registry.get("review")
        entry.abort_event = new_abort
        registry.set_state("review", "processing")
        await send_audio_end(True)

    monkeypatch.setattr(button_dispatch, "run_text_turn", run)
    await button_dispatch._dispatch_prompt("review", "Hello", None, ChannelServer())
    completion.assert_not_awaited()
    assert registry.get("review").abort_event is new_abort
    assert registry.get("review").state == "processing"


async def test_aborted_prompt_does_not_reopen_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry.register("review", ws=SimpleNamespace(closed=False, send_str=AsyncMock()))
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "get_manager", lambda: manager)
    completion = AsyncMock()
    monkeypatch.setattr(manager, "send_prompt_audio_end", completion)

    async def run(
        *_args: object, send_audio_end: Callable[[bool], Awaitable[None]],
    ) -> None:
        registry.get("review").abort_event.set()
        await send_audio_end(True)

    monkeypatch.setattr(button_dispatch, "run_text_turn", run)
    await button_dispatch._dispatch_prompt("review", "Hello", None, ChannelServer())
    completion.assert_not_awaited()
    assert registry.get("review").abort_event is None
    assert registry.get("review").state == "idle"
