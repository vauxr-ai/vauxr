"""Action-button dispatch: prompt / announce / command / webhook."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import button_dispatch
import config as cfg_mod
import device_registry as registry
import webhooks
from channel_server import ChannelServer


class FakeWs:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.binary: list[bytes] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.text.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(bytes(data))


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    registry.reset()
    webhooks.reset_for_tests()
    webhooks.load()
    yield
    registry.reset()
    webhooks.reset_for_tests()
    cfg_mod.reset_config()


async def test_unmapped_gesture_is_noop() -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert ws.text == []


async def test_command_mute_sends_device_control() -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"long_press": {"kind": "command", "command": "mute"}}},
    )
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="long_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert any('"command":"mute"' in t or '"command": "mute"' in t for t in ws.text)


async def test_prompt_calls_run_text_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "lights off"}}},
    )
    called: list[str] = []

    async def fake_run(device_id, text, *_a, **_k):
        called.append(text)

    monkeypatch.setattr(button_dispatch, "run_text_turn", fake_run)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert called == ["lights off"]


async def test_prompt_dropped_when_listening(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    entry = registry.register("dev1", ws=ws)
    entry.state = "listening"
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )
    fake = AsyncMock()
    monkeypatch.setattr(button_dispatch, "run_text_turn", fake)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    fake.assert_not_called()


async def test_prompt_uses_ws_turn_even_with_live_realtime_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warm-quiet keeps the WebRTC mic paused; prompts speak over WS 0x02."""
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )
    manager = Mock()
    manager.has_live_session.return_value = True
    manager.seed_text_turn = AsyncMock()
    monkeypatch.setattr("realtime_session.get_manager", lambda: manager)
    fake = AsyncMock()
    monkeypatch.setattr(button_dispatch, "run_text_turn", fake)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    manager.seed_text_turn.assert_not_awaited()
    fake.assert_awaited_once()
    assert fake.await_args.args[0] == "dev1"
    assert fake.await_args.args[1] == "hi"
    assert registry.get("dev1").state == "idle"


async def test_prompt_second_dropped_while_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_turn(*_a: object, **_k: object) -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(button_dispatch, "run_text_turn", fake_turn)
    first = asyncio.create_task(
        button_dispatch.handle_device_button(
            device_id="dev1",
            button="action",
            gesture="double_press",
            openclaw_client=None,
            channel_server=ChannelServer(),
        )
    )
    await started.wait()
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert calls == 1
    assert registry.get("dev1").state == "processing"
    release.set()
    await first
    assert registry.get("dev1").state == "idle"


async def test_prompt_failure_restores_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("tts failed")

    monkeypatch.setattr(button_dispatch, "run_text_turn", boom)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert registry.get("dev1").state == "idle"


async def test_webhook_posts_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    hook = webhooks.create("HA", "http://ha.example/hook", "Bearer tok")
    registry.update_config(
        "dev1",
        {"button_actions": {"triple_press": {"kind": "webhook", "webhook_id": hook.id}}},
    )

    posted: list[dict[str, Any]] = []

    class FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json, headers):
            posted.append({"url": url, "json": json, "headers": headers})
            return FakeResp()

    monkeypatch.setattr(button_dispatch.aiohttp, "ClientSession", FakeSession)

    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="triple_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert len(posted) == 1
    assert posted[0]["url"] == "http://ha.example/hook"
    assert posted[0]["json"]["gesture"] == "triple_press"
    assert posted[0]["json"]["device_id"] == "dev1"
    assert posted[0]["headers"]["Authorization"] == "Bearer tok"


async def test_webhook_posts_configured_body(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    hook = webhooks.create(
        "Lights low",
        "http://ha.example/api/services/scene/turn_on",
        "Bearer tok",
        {"entity_id": "scene.lights_low"},
    )
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "webhook", "webhook_id": hook.id}}},
    )
    posted: list[dict[str, Any]] = []

    class FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json, headers):
            posted.append({"url": url, "json": json, "headers": headers})
            return FakeResp()

    monkeypatch.setattr(button_dispatch.aiohttp, "ClientSession", FakeSession)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert posted[0]["json"] == {"entity_id": "scene.lights_low"}
    assert posted[0]["url"] == "http://ha.example/api/services/scene/turn_on"


@pytest.mark.parametrize("follow_up", [False, True])
@pytest.mark.parametrize("paused", [False, True])
async def test_warm_prompt_emits_ws_audio_and_records_conversation(
    monkeypatch: pytest.MonkeyPatch, follow_up: bool, paused: bool,
) -> None:
    import json
    from collections.abc import AsyncIterator, Callable
    from types import SimpleNamespace

    import pipeline
    import realtime_session

    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config("dev1", {
        "button_actions": {"double_press": {"kind": "prompt", "text": "Say hello"}},
    })
    manager = realtime_session.RealtimeManager()
    session = realtime_session.RealtimeSession("dev1", channel_server=object())
    session.set_mic_paused(paused)
    manager._sessions["dev1"] = session
    monkeypatch.setattr(session, "is_peer_live", lambda: True)
    monkeypatch.setattr(realtime_session, "get_manager", lambda: manager)
    seed = AsyncMock()
    monkeypatch.setattr(manager, "seed_text_turn", seed)
    channel = ChannelServer()
    monkeypatch.setattr(channel, "get_active_channel", lambda: SimpleNamespace(type="openclaw-direct"))

    async def chat(_key: str, _text: str, on_delta: Callable[[str], None]) -> None:
        on_delta("Hello?" if follow_up else "Hello.")

    async def synthesize(_text: str, **_kwargs: object) -> AsyncIterator[bytes]:
        yield b"\x01\x00" * 320

    monkeypatch.setattr(pipeline, "synthesize", synthesize)
    await button_dispatch.handle_device_button(
        device_id="dev1", button="action", gesture="double_press",
        openclaw_client=SimpleNamespace(chat=chat), channel_server=channel,
    )
    seed.assert_not_awaited()
    assert ws.binary and all(frame[0] == 0x02 for frame in ws.binary)
    assert any(json.loads(text)["type"] == "audio.end" for text in ws.text)
    assert manager.context_messages("dev1") == [
        {"role": "user", "content": "Say hello"},
        {"role": "assistant", "content": "Hello?" if follow_up else "Hello."},
    ]
    assert registry.get("dev1").state == ("listening" if follow_up else "idle")
    assert session._mic_paused is (paused and not follow_up)
    if follow_up:
        assert not session._turns_suppressed()
    assert not session.is_closed
