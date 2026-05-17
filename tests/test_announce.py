"""Phase 13: HTTP announce + device.command + channel CRUD endpoints."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vauxr import (
    channel_registry,
    config as cfg_mod,
    device_registry as registry,
    pipeline,
    wyoming_tts,
)
from http_server import make_http_app


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
    monkeypatch.setenv("DEVICE_TOKEN", "tok-X")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    registry.reset()
    channel_registry._reset_for_tests()
    channel_registry.load()

    # Stub TTS so we don't talk to a real Piper.
    async def fake_synth(text: str, **_k):
        for _ in range(3):
            yield b"\x00\x01" * 50

    monkeypatch.setattr(wyoming_tts, "synthesize", fake_synth)
    # The http_server module imports synthesize at module load; rebind via the
    # http_server namespace too.
    from vauxr import http_server as hs

    monkeypatch.setattr(hs, "synthesize", fake_synth)

    yield
    registry.reset()
    channel_registry._reset_for_tests()
    cfg_mod.reset_config()


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    app = make_http_app()
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok-X"}


def _now() -> datetime:
    return datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


# --- announce ---


async def test_announce_unknown_device_404(client: TestClient) -> None:
    res = await client.post("/api/devices/no-such/announce", headers=_auth(), json={"text": "hi"})
    assert res.status == 404


async def test_announce_busy_device_409(client: TestClient) -> None:
    ws = FakeWs()
    entry = registry.register("dev1", ws=ws)
    entry.state = "listening"
    res = await client.post("/api/devices/dev1/announce", headers=_auth(), json={"text": "hi"})
    assert res.status == 409


async def test_announce_missing_text_400(client: TestClient) -> None:
    registry.register("dev1", ws=FakeWs())
    res = await client.post("/api/devices/dev1/announce", headers=_auth(), json={})
    assert res.status == 400


async def test_announce_sends_audio_frames_and_end(client: TestClient) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    res = await client.post("/api/devices/dev1/announce", headers=_auth(), json={"text": "hello"})
    assert res.status == 200
    # All binary frames have type 0x03 (push audio).
    assert ws.binary
    for frame in ws.binary:
        assert frame[0] == 0x03
    # audio.end fired.
    end_msgs = [json.loads(t) for t in ws.text if "audio.end" in t]
    assert any(m.get("type") == "audio.end" for m in end_msgs)


# --- device command ---


async def test_command_unknown_device(client: TestClient) -> None:
    res = await client.post("/api/devices/no/command", headers=_auth(), json={"command": "mute"})
    assert res.status == 404


async def test_command_invalid_command(client: TestClient) -> None:
    registry.register("dev1", ws=FakeWs())
    res = await client.post(
        "/api/devices/dev1/command", headers=_auth(), json={"command": "nope"}
    )
    assert res.status == 400


async def test_command_missing_command(client: TestClient) -> None:
    registry.register("dev1", ws=FakeWs())
    res = await client.post("/api/devices/dev1/command", headers=_auth(), json={})
    assert res.status == 400


async def test_command_forwards_to_device(client: TestClient) -> None:
    ws = FakeWs()
    registry.register("dev1", ws=ws)
    res = await client.post(
        "/api/devices/dev1/command",
        headers=_auth(),
        json={"command": "set_volume", "params": {"level": 0.7}},
    )
    assert res.status == 200
    sent = [json.loads(t) for t in ws.text]
    cmd = next(m for m in sent if m.get("type") == "device.control")
    assert cmd["command"] == "set_volume"
    assert cmd["params"] == {"level": 0.7}


# --- PATCH /api/devices/{id} ---


async def test_patch_device_updates_config(client: TestClient) -> None:
    entry = registry.register("dev1", ws=FakeWs())
    entry.last_seen = _now()
    res = await client.patch(
        "/api/devices/dev1",
        headers=_auth(),
        json={"name": "Kitchen", "voice": True, "follow_up_mode": "always"},
    )
    assert res.status == 200
    body = await res.json()
    assert body["name"] == "Kitchen"
    assert body["config"]["name"] == "Kitchen"
    assert body["config"]["voice"] is True
    assert body["config"]["follow_up_mode"] == "always"


async def test_patch_device_invalid_follow_up_mode(client: TestClient) -> None:
    registry.register("dev1", ws=FakeWs())
    res = await client.patch(
        "/api/devices/dev1", headers=_auth(), json={"follow_up_mode": "garbage"}
    )
    assert res.status == 400


async def test_patch_device_invalid_name_type(client: TestClient) -> None:
    registry.register("dev1", ws=FakeWs())
    res = await client.patch("/api/devices/dev1", headers=_auth(), json={"name": 5})
    assert res.status == 400


async def test_patch_unknown_device_404(client: TestClient) -> None:
    res = await client.patch("/api/devices/missing", headers=_auth(), json={"name": "x"})
    assert res.status == 404


# --- /api/channels CRUD ---


async def test_list_channels_empty(client: TestClient) -> None:
    res = await client.get("/api/channels", headers=_auth())
    assert res.status == 200
    assert await res.json() == []


async def test_create_channel(client: TestClient) -> None:
    res = await client.post("/api/channels", headers=_auth(), json={"name": "My Channel"})
    assert res.status == 201
    body = await res.json()
    assert body["name"] == "My Channel"
    assert body["type"] == "openclaw"
    assert body["token"].startswith("vx_ch_")


async def test_create_channel_missing_name(client: TestClient) -> None:
    res = await client.post("/api/channels", headers=_auth(), json={})
    assert res.status == 400


async def test_delete_channel(client: TestClient) -> None:
    created = await (await client.post("/api/channels", headers=_auth(), json={"name": "X"})).json()
    res = await client.delete(f"/api/channels/{created['id']}", headers=_auth())
    assert res.status == 200


async def test_delete_nonexistent_channel(client: TestClient) -> None:
    res = await client.delete("/api/channels/does-not-exist", headers=_auth())
    assert res.status == 404


async def test_activate_channel(client: TestClient) -> None:
    created = await (await client.post("/api/channels", headers=_auth(), json={"name": "X"})).json()
    res = await client.post(f"/api/channels/{created['id']}/activate", headers=_auth())
    assert res.status == 200


async def test_rotate_token(client: TestClient) -> None:
    created = await (await client.post("/api/channels", headers=_auth(), json={"name": "X"})).json()
    res = await client.post(f"/api/channels/{created['id']}/rotate", headers=_auth())
    assert res.status == 200
    body = await res.json()
    assert body["token"].startswith("vx_ch_")
