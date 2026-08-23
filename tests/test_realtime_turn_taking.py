"""Realtime turn-taking: conversation log + hello policy extras + cold routing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

import config as cfg_mod
import device_registry as dev_reg
from realtime_session import RealtimeManager
from server import make_app


# --- conversation log (record_turn choke point) ---


def _log(mgr: RealtimeManager, device_id: str) -> list[dict[str, str]]:
    return mgr.context_messages(device_id)


def test_record_turn_appends_user_and_assistant() -> None:
    mgr = RealtimeManager()
    mgr.record_turn("dev", "hello there", "hi, how can I help?")
    assert _log(mgr, "dev") == [
        {"role": "user", "content": "hello there"},
        {"role": "assistant", "content": "hi, how can I help?"},
    ]


def test_record_turn_strips_follow_up_tag() -> None:
    mgr = RealtimeManager()
    mgr.record_turn("dev", "what's the weather", "It's sunny. [[follow_up]]")
    log = _log(mgr, "dev")
    assert log[-1]["content"] == "It's sunny."
    assert "[[follow_up]]" not in log[-1]["content"]


def test_record_turn_alternates_roles_across_turns() -> None:
    mgr = RealtimeManager()
    mgr.record_turn("dev", "one", "first reply")
    mgr.record_turn("dev", "two", "second reply")
    roles = [m["role"] for m in _log(mgr, "dev")]
    assert roles == ["user", "assistant", "user", "assistant"]


def test_record_turn_skips_empty() -> None:
    mgr = RealtimeManager()
    mgr.record_turn("dev", "", "")
    assert _log(mgr, "dev") == []


def test_record_turn_user_only_then_assistant_only() -> None:
    mgr = RealtimeManager()
    mgr.record_turn("dev", "just user", "")
    mgr.record_turn("dev", "", "just assistant")
    assert _log(mgr, "dev") == [
        {"role": "user", "content": "just user"},
        {"role": "assistant", "content": "just assistant"},
    ]


def test_record_turn_collapses_consecutive_user_refinalize() -> None:
    # A VAD re-finalize re-records the same user turn before the reply lands;
    # it must overwrite rather than create back-to-back user messages.
    mgr = RealtimeManager()
    mgr.record_turn("dev", "partial", "")
    mgr.record_turn("dev", "partial final", "")
    log = _log(mgr, "dev")
    assert log == [{"role": "user", "content": "partial final"}]


class _FakeWs:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.closed = False

    async def send_str(self, data: str) -> None:
        self.text.append(data)


def _audio_ends(ws: _FakeWs) -> list[dict[str, object]]:
    return [json.loads(t) for t in ws.text if "audio.end" in t]


@pytest.mark.asyncio
async def test_empty_timeout_completion_does_not_force_follow_up() -> None:
    """Channel timeout completes with (False, ''); do not reopen the mic."""
    from realtime_session import RealtimeSession

    ws = _FakeWs()
    dev_reg.register("dev-empty", ws=ws)
    try:
        session = RealtimeSession("dev-empty", channel_server=object())
        await session._on_turn_complete(False, "")
        ends = _audio_ends(ws)
        assert ends
        assert ends[-1]["type"] == "audio.end"
        assert ends[-1]["follow_up"] is False
        entry = dev_reg.get("dev-empty")
        assert entry is not None
        assert entry.state == "idle"
    finally:
        dev_reg.unregister("dev-empty")


@pytest.mark.asyncio
async def test_idle_interruption_does_not_force_follow_up() -> None:
    """Pipecat interrupts on every user-turn start; that must not reopen the mic."""
    from realtime_session import RealtimeSession

    ws = _FakeWs()
    dev_reg.register("dev-idle-int", ws=ws)
    try:
        session = RealtimeSession("dev-idle-int", channel_server=object())
        session._on_interruption()
        assert session._user_barged_in is False
        session._bot_stop_credits = 1
        await session._on_turn_complete(False, "Glad you like it! \U0001f5a4")
        ends = _audio_ends(ws)
        assert ends
        assert ends[-1]["follow_up"] is False
        entry = dev_reg.get("dev-idle-int")
        assert entry is not None
        assert entry.state == "idle"
    finally:
        session._cancel_drain_timer()
        dev_reg.unregister("dev-idle-int")


@pytest.mark.asyncio
async def test_barge_in_during_reply_keeps_listening() -> None:
    """A real cut-in over TTS still overrides a follow_up=false end."""
    from realtime_session import RealtimeSession

    ws = _FakeWs()
    dev_reg.register("dev-barge-int", ws=ws)
    try:
        session = RealtimeSession("dev-barge-int", channel_server=object())
        session._bot_speaking = 1
        session._on_interruption()
        assert session._user_barged_in is True
        session._bot_speaking = 0
        session._bot_stop_credits = 1
        await session._on_turn_complete(False, "Glad you like it!")
        ends = _audio_ends(ws)
        assert ends
        assert ends[-1]["follow_up"] is True
        entry = dev_reg.get("dev-barge-int")
        assert entry is not None
        assert entry.state == "listening"
    finally:
        session._cancel_drain_timer()
        dev_reg.unregister("dev-barge-int")


def test_turns_suppressed_when_barge_in_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from realtime_session import RealtimeSession

    monkeypatch.setattr(dev_reg, "get_config_for", lambda _id: {"barge_in": False})
    session = RealtimeSession("dev-bi", channel_server=object())
    session._awaiting_reply = False
    session._bot_speaking = 1
    assert session._turns_suppressed() is True
    session._bot_speaking = 0
    assert session._turns_suppressed() is False


def test_turns_not_suppressed_during_tts_when_barge_in_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from realtime_session import RealtimeSession

    monkeypatch.setattr(dev_reg, "get_config_for", lambda _id: {"barge_in": True})
    session = RealtimeSession("dev-bi", channel_server=object())
    session._awaiting_reply = False
    session._bot_speaking = 1
    assert session._turns_suppressed() is False
    session._awaiting_reply = True
    assert session._turns_suppressed() is True


def test_context_messages_is_a_copy() -> None:
    mgr = RealtimeManager()
    mgr.record_turn("dev", "u", "a")
    snap = mgr.context_messages("dev")
    snap.append({"role": "user", "content": "mutated"})
    assert len(mgr.context_messages("dev")) == 2


def test_is_cold_wait_lifecycle() -> None:
    mgr = RealtimeManager()
    assert mgr.is_cold_wait("dev") is False
    mgr.begin_preroll("dev")
    assert mgr.is_cold_wait("dev") is True
    mgr.add_preroll("dev", b"\x00" * 100)
    assert mgr.take_preroll("dev") == b"\x00" * 100


async def test_ws_pipeline_records_turn_into_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed WS turn must flow through the record_turn choke point."""
    import channel_registry
    import pipeline
    import realtime_session
    import wyoming_stt
    import wyoming_tts
    from channel_server import ChannelServer
    from pipeline import run_voice_turn

    # Fresh manager singleton so the log starts empty.
    realtime_session._manager = None  # type: ignore[attr-defined]

    ch = channel_registry.Channel(
        id="ch-1",
        name="My Channel",
        type="openclaw",
        tokenHash="hash",
        active=True,
        createdAt="2026-05-17T00:00:00Z",
    )
    channel_registry._set_active_for_tests(ch)

    async def fake_stt(*_a, **_k):
        return "what's the weather"

    monkeypatch.setattr(wyoming_stt, "transcribe", fake_stt)
    monkeypatch.setattr(pipeline, "transcribe", fake_stt)

    async def fake_tts(text: str, **_k):
        yield b"audio"

    monkeypatch.setattr(wyoming_tts, "synthesize", fake_tts)
    monkeypatch.setattr(pipeline, "synthesize", fake_tts)

    class _Ws:
        closed = False

        async def send_str(self, _s: str) -> None: ...
        async def send_bytes(self, _b: bytes) -> None: ...

    ws = _Ws()
    dev_reg.register("dev-rec", ws=ws)

    cs = ChannelServer()

    def send_transcript_stub(device_id: str, _text: str) -> bool:
        import asyncio

        async def feed() -> None:
            for _ in range(50):
                if cs.get_response_listener(device_id) is not None:
                    break
                await asyncio.sleep(0.005)
            listener = cs.get_response_listener(device_id)
            assert listener is not None
            listener["on_delta"]("run-1", "It is sunny. [[follow_up]]")
            listener["on_end"]("run-1")

        asyncio.create_task(feed())
        return True

    cs.send_transcript = send_transcript_stub  # type: ignore[method-assign]

    import asyncio

    await run_voice_turn("dev-rec", [b"\x00" * 100], ws, None, cs, asyncio.Event())

    log = realtime_session.get_manager().context_messages("dev-rec")
    assert {"role": "user", "content": "what's the weather"} in log
    # follow_up tag stripped in the stored assistant text.
    assistant = [m for m in log if m["role"] == "assistant"]
    assert assistant and assistant[-1]["content"] == "It is sunny."

    dev_reg.unregister("dev-rec")
    channel_registry._set_active_for_tests(None)
    realtime_session._manager = None  # type: ignore[attr-defined]


# --- hello policy extras ---


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "ws-test-token")
    monkeypatch.setenv("REALTIME_ENABLED", "1")
    monkeypatch.setenv("REALTIME_HOST", "192.168.1.50")
    yield
    cfg_mod.reset_config()
    dev_reg.unregister("dev1")


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    app = make_app()
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def _recv_json(ws) -> dict:
    msg = await ws.receive(timeout=2)
    assert msg.type == WSMsgType.TEXT, f"got {msg.type}: {msg.data!r}"
    return json.loads(msg.data)


async def test_hello_realtime_policy_includes_taper_and_vad(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "dev1",
                "token": "ws-test-token",
                "platform": "satellite1",
                "caps": ["ws", "webrtc"],
            }
        )
        hello = await _recv_json(ws)
        assert hello["type"] == "hello"
        policy = hello["realtime"]
        assert policy["enabled"] is True
        assert policy["transport"] == "webrtc"
        assert "taper" in policy and "vad" in policy
        assert policy["taper"]["t_idle1_ms"] > 0
        assert "confidence" in policy["vad"]


async def test_hello_registers_device_identity(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "dev1",
                "token": "ws-test-token",
                "platform": "satellite1",
                "fw_version": "abc123-dirty",
                "caps": ["ws", "webrtc", "ota"],
            }
        )
        hello = await _recv_json(ws)
        assert hello["type"] == "hello"
        entry = dev_reg.get("dev1")
        assert entry is not None
        assert entry.platform == "satellite1"
        assert entry.fw_version == "abc123-dirty"
        # Idle announce/TTS needs the sink rate before the first voice turn.
        assert entry.output_sample_rate == 48000


async def test_hello_output_sample_rate_overrides_platform(client: TestClient) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "dev1",
                "token": "ws-test-token",
                "platform": "satellite1",
                "output_sample_rate": 24000,
                "caps": ["ws"],
            }
        )
        await _recv_json(ws)
        entry = dev_reg.get("dev1")
        assert entry is not None
        assert entry.output_sample_rate == 24000


async def test_hello_ws_only_policy_has_no_extras(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with client.ws_connect("/ws") as ws:
        await ws.send_json(
            {
                "type": "hello",
                "device_id": "dev1",
                "token": "ws-test-token",
                "platform": "satellite1",
                "caps": ["ws"],
            }
        )
        hello = await _recv_json(ws)
        policy = hello["realtime"]
        assert policy["enabled"] is False
        assert "taper" not in policy
        assert "vad" not in policy
