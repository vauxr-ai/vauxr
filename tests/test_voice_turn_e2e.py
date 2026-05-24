"""Phase 13 / 14: end-to-end voice-turn against the combined WS+HTTP app."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

import channel_registry
import config as cfg_mod
import device_registry as registry
import pipeline
import wyoming_stt
import wyoming_tts
from server import APP_STATE, make_app


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok-E")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "wss://stub.invalid/")
    registry.reset()
    channel_registry._reset_for_tests()
    channel_registry._openclaw_direct_active = True  # type: ignore[attr-defined]
    yield
    channel_registry._reset_for_tests()
    registry.reset()
    cfg_mod.reset_config()


@pytest.fixture
async def setup(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TestClient]:
    # Stub STT / TTS / OpenClaw.
    async def fake_transcribe(_chunks, sample_rate=16000):
        return "what is the weather"

    async def fake_synth(text: str, **_k):
        yield b"\x01\x02\x03\x04"

    monkeypatch.setattr(wyoming_stt, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    monkeypatch.setattr(wyoming_tts, "synthesize", fake_synth)
    monkeypatch.setattr(pipeline, "synthesize", fake_synth)

    class FakeOpenClaw:
        async def chat(self, sk, msg, on_delta):
            on_delta("It is sunny.")

    app = make_app()
    app[APP_STATE].openclaw_client = FakeOpenClaw()  # type: ignore[assignment]

    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def test_full_voice_turn_through_server(setup: TestClient) -> None:
    client = setup
    async with client.ws_connect("/ws") as ws:
        await ws.send_json({"type": "voice.start", "device_id": "dev1", "token": "tok-E"})
        ready = await ws.receive(timeout=2)
        assert ready.type == WSMsgType.TEXT
        assert json.loads(ready.data) == {"type": "ready"}

        # Send some "audio".
        await ws.send_bytes(b"\x01\x00\x00" + (b"\x00\x00" * 800))
        await ws.send_json({"type": "voice.end"})

        # Collect messages until audio.end.
        received: list = []
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            msg = await ws.receive(timeout=2)
            received.append(msg)
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "audio.end":
                    break
        text_msgs = [json.loads(m.data) for m in received if m.type == WSMsgType.TEXT]
        types = [m["type"] for m in text_msgs]
        assert "transcript" in types
        assert "audio.end" in types
        # At least one binary 0x02 frame.
        bins = [m for m in received if m.type == WSMsgType.BINARY]
        assert any(b.data[0] == 0x02 for b in bins)
