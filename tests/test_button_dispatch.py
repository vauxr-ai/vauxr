"""Action-button dispatch: prompt / announce / command / webhook."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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


async def test_prompt_seeds_idle_warm_realtime_session(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )
    seeded: list[tuple[str, str]] = []

    class _Mgr:
        def has_live_session(self, device_id: str) -> bool:
            return device_id == "dev1"

        async def seed_text_turn(self, device_id: str, text: str) -> bool:
            seeded.append((device_id, text))
            return True

    monkeypatch.setattr("realtime_session.get_manager", lambda: _Mgr())
    fake = AsyncMock()
    monkeypatch.setattr(button_dispatch, "run_text_turn", fake)
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert seeded == [("dev1", "hi")]
    fake.assert_not_called()
    assert registry.get("dev1").state == "processing"


async def test_prompt_second_seed_dropped_while_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )
    seeded: list[str] = []

    class _Mgr:
        def has_live_session(self, device_id: str) -> bool:
            return device_id == "dev1"

        async def seed_text_turn(self, device_id: str, text: str) -> bool:
            seeded.append(text)
            return True

    monkeypatch.setattr("realtime_session.get_manager", lambda: _Mgr())
    fake = AsyncMock()
    monkeypatch.setattr(button_dispatch, "run_text_turn", fake)

    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    await button_dispatch.handle_device_button(
        device_id="dev1",
        button="action",
        gesture="double_press",
        openclaw_client=None,
        channel_server=ChannelServer(),
    )
    assert seeded == ["hi"]
    fake.assert_not_called()
    assert registry.get("dev1").state == "processing"


async def test_prompt_seed_miss_restores_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    registry.update_config(
        "dev1",
        {"button_actions": {"double_press": {"kind": "prompt", "text": "hi"}}},
    )

    class _Mgr:
        def has_live_session(self, device_id: str) -> bool:
            return True

        async def seed_text_turn(self, device_id: str, text: str) -> bool:
            return False

    monkeypatch.setattr("realtime_session.get_manager", lambda: _Mgr())
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
