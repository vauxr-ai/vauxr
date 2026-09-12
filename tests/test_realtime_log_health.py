"""Exercise log policy against the real, optional Pipecat dependency."""

import asyncio
import json
from collections.abc import Iterator
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")

from aiortc.mediastreams import MediaStreamError
from av import AudioFrame
from loguru import logger
from pipecat.frames.frames import StartFrame
from pipecat.services.ai_service import AIService
from pipecat.services.settings import STTSettings
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection, SmallWebRTCTrack
from pipecat.transports.smallwebrtc.transport import SmallWebRTCClient
from pipecat.utils.types import NOT_GIVEN

import config
import device_registry
from realtime_llm import ChannelLLMService
from realtime_session import RealtimeSession
from realtime_transport import use_websocket_control
from realtime_wyoming import WyomingSTTService, WyomingTTSService


@pytest.fixture(autouse=True)
def session_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DEVICE_TOKEN", "log-health-test")
    monkeypatch.setenv("REALTIME_ESP32", "1")
    config.reset_config()
    yield
    config.reset_config()
    device_registry.unregister("log-health-test")


@pytest.fixture
def control_ws() -> SimpleNamespace:
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    device_registry.register("log-health-test", ws=ws)
    return ws


@pytest.fixture
def messages() -> Iterator[list[str]]:
    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        yield records
    finally:
        logger.remove(sink)


async def test_service_store_settings_are_complete(
    monkeypatch: pytest.MonkeyPatch, messages: list[str]
) -> None:
    monkeypatch.setenv("DEVICE_TOKEN", "log-health-test")
    config.reset_config()
    try:
        services = [
            WyomingSTTService(),
            WyomingTTSService(),
            ChannelLLMService(device_id="test", channel_server=SimpleNamespace()),
        ]
        for service in services:
            # Exercise the same validation that runs on pipeline startup.
            await AIService.start(service, StartFrame())
            assert all(
                getattr(service._settings, field.name) is not NOT_GIVEN
                for field in fields(service._settings)
            )
        assert not messages
        # Validation remains active for genuinely incomplete stores elsewhere.
        STTSettings().validate_complete()
        assert any("NOT_GIVEN" in message for message in messages)
    finally:
        config.reset_config()


async def test_ws_control_is_instance_local_and_preserves_browser_timeout(
    monkeypatch: pytest.MonkeyPatch, messages: list[str]
) -> None:
    monkeypatch.setattr(
        "pipecat.transports.smallwebrtc.connection.DATA_CHANNEL_TIMEOUT_SECS", 0
    )
    esp = SmallWebRTCConnection()
    browser = SmallWebRTCConnection()
    try:
        esp.send_app_message({"test": "queued before policy"})
        esp._start_data_channel_timeout()
        old_watchdog = esp._data_channel_timeout_task
        use_websocket_control(esp)
        await asyncio.sleep(0)
        assert old_watchdog.cancelled()
        for _ in range(2):
            esp._start_data_channel_timeout()
            esp.send_app_message({"test": "unused control"})
            assert esp._data_channel_timeout_task is None
            assert esp._outgoing_messages_queue == []
            # Exercise the actual close/reinitialize path used by peer restart.
            await esp._close()
            esp._initialize()
        browser.send_app_message({"test": "browser still queues"})
        browser._start_data_channel_timeout()
        assert len(browser._outgoing_messages_queue) == 1
        assert browser._data_channel_timeout_task is not None
        assert esp._handle_new_connection_state.__func__ is browser._handle_new_connection_state.__func__
        await browser._data_channel_timeout_task
        assert browser._outgoing_messages_queue == []
        assert any("Data channel not established" in message for message in messages)
    finally:
        browser._cancel_data_channel_timeout()
        await esp._pc.close()
        await browser._pc.close()


async def test_ws_control_preserves_failed_peer_cleanup(messages: list[str]) -> None:
    connection = SmallWebRTCConnection()
    peer = connection._pc
    use_websocket_control(connection)
    connection._pc = SimpleNamespace(connectionState="failed")
    connection._close = AsyncMock()
    try:
        await connection._handle_new_connection_state()
        connection._close.assert_awaited_once()
        assert any("Connection failed" in message for message in messages)
    finally:
        await peer.close()


def _audio_track() -> SmallWebRTCTrack:
    frame = AudioFrame(format="s16", layout="mono", samples=320)
    frame.sample_rate = 16000
    frame.planes[0].update(bytes(640))
    remote = SimpleNamespace(kind="audio", recv=AsyncMock(return_value=frame), stop=lambda: None)
    return SmallWebRTCTrack(SimpleNamespace(track=remote))


async def test_warm_quiet_still_reads_audio_and_follow_up_reenables_track(
    control_ws: SimpleNamespace,
) -> None:
    track = _audio_track()
    session = RealtimeSession("log-health-test", channel_server=SimpleNamespace())
    session._connection = SimpleNamespace(audio_input_track=lambda: track)
    try:
        await session._send_audio_end(False)
        assert not track.is_enabled()
        assert json.loads(control_ws.send_str.call_args.args[0]) == {
            "type": "audio.end", "follow_up": False,
        }
        # Actual Pipecat audio recv must work even while the flag is False.
        assert isinstance(await track.recv(), AudioFrame)
        # Late RTP frames do not undo quiet; speech onset explicitly restores it.
        assert not track.is_enabled()
        assert not session._turns_suppressed()  # RTP-only warm wake can start a turn.
        session._set_audio_input_expected(True)
        assert track.is_enabled()
        await session._send_audio_end(False)
        await session._send_audio_end(True)
        assert track.is_enabled()
        assert not session.is_closed
    finally:
        track.stop()
        device_registry.unregister("log-health-test")


@pytest.mark.parametrize("initially_enabled", [False, True])
async def test_browser_audio_policy_preserves_track_ownership(
    monkeypatch: pytest.MonkeyPatch, control_ws: SimpleNamespace,
    messages: list[str], initially_enabled: bool,
) -> None:
    monkeypatch.setenv("REALTIME_ESP32", "0")
    config.reset_config()
    track = _audio_track()
    track.set_enabled(initially_enabled)
    session = RealtimeSession("log-health-test", channel_server=SimpleNamespace())
    session._connection = SimpleNamespace(audio_input_track=lambda: track)
    await session._send_audio_end(False)
    assert track.is_enabled() is initially_enabled
    assert not session._turns_suppressed()
    await session._send_audio_end(True)
    session._set_audio_input_expected(True)
    assert track.is_enabled() is initially_enabled
    await _assert_audio_diagnostics(track, initially_enabled, messages)


@pytest.mark.parametrize("failure", ["missing", "closed", "reset", "runtime"])
@pytest.mark.parametrize("initially_enabled", [False, True])
async def test_failed_audio_end_keeps_timeout_diagnostics(
    control_ws: SimpleNamespace, messages: list[str], failure: str, initially_enabled: bool,
) -> None:
    track = _audio_track()
    track.set_enabled(initially_enabled)
    session = RealtimeSession("log-health-test", channel_server=SimpleNamespace())
    session._connection = SimpleNamespace(audio_input_track=lambda: track)
    if failure == "missing":
        device_registry.unregister("log-health-test")
    elif failure == "closed":
        control_ws.closed = True
    else:
        control_ws.send_str.side_effect = ConnectionResetError() if failure == "reset" else RuntimeError()
    device_registry.set_state("log-health-test", "processing")
    await session._send_audio_end(False)
    assert track.is_enabled()
    assert not session._mic_paused
    entry = device_registry.get("log-health-test")
    if entry is not None:
        assert entry.state == "processing"
    if failure in ("missing", "closed"):
        control_ws.send_str.assert_not_awaited()
    else:
        control_ws.send_str.assert_awaited_once()
    await _assert_audio_diagnostics(track, True, messages)


@pytest.mark.parametrize(
    "intervening", ["speech", "follow_up", "resume", "pause", "closed", "ended", "cancel", "none"],
)
async def test_pending_audio_end_does_not_hide_new_activity(
    control_ws: SimpleNamespace, intervening: str,
) -> None:
    track = _audio_track()
    session = RealtimeSession("log-health-test", channel_server=SimpleNamespace())
    session._connection = SimpleNamespace(audio_input_track=lambda: track)
    started, release = asyncio.Event(), asyncio.Event()

    async def send(data: str) -> None:
        if not json.loads(data)["follow_up"]:
            started.set()
            await release.wait()

    control_ws.send_str.side_effect = send
    task = asyncio.create_task(session._send_audio_end(False))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert track.is_enabled()
        if intervening == "speech":
            session._set_audio_input_expected(True)
        elif intervening == "follow_up":
            await session._send_audio_end(True)
        elif intervening in ("closed", "ended"):
            setattr(session, "_closed" if intervening == "closed" else "_ended_notified", True)
            device_registry.set_state("log-health-test", "processing")
        elif intervening in ("resume", "pause"):
            session.set_mic_paused(intervening == "pause")
        elif intervening == "cancel":
            task.cancel()
        release.set()
        if intervening == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, timeout=1)
        assert track.is_enabled() is (intervening not in ("none", "pause"))
        assert session._mic_paused is (intervening == "pause")
        if intervening == "follow_up":
            assert device_registry.get("log-health-test").state == "listening"
        if intervening in ("closed", "ended"):
            assert device_registry.get("log-health-test").state == "processing"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        track.stop()


@pytest.mark.parametrize("expected", [False, True])
async def test_audio_timeout_policy_preserves_media_errors(
    expected: bool, messages: list[str]
) -> None:
    track = _audio_track()
    track.set_enabled(expected)
    await _assert_audio_diagnostics(track, expected, messages)


async def _assert_audio_diagnostics(
    track: SmallWebRTCTrack, expected: bool, messages: list[str],
) -> None:
    audio = await track.recv()
    track._track.recv.side_effect = [TimeoutError(), audio, MediaStreamError()]
    client = SmallWebRTCClient(
        SimpleNamespace(event_handler=lambda event: lambda callback: callback, is_connected=lambda: True),
        SimpleNamespace(),
    )
    client._audio_input_track = track
    client._audio_in_resampler = object()
    client._in_sample_rate = 16000
    client._audio_in_layout = "mono"
    client._audio_in_channels = 1
    reader = client.read_audio_frame()
    try:
        assert (await anext(reader)).audio == bytes(640)
        assert any("No audio frame" in message for message in messages) is expected
        # A dead track must still warn and be cleared, including during quiet.
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(reader), timeout=0.05)
        assert client._audio_input_track is None
        assert any("Media stream error" in message for message in messages)
    finally:
        await reader.aclose()
        track.stop()


async def test_rtp_only_warm_wake_can_promote_new_turn(control_ws: SimpleNamespace) -> None:
    from pipecat.frames.frames import VADUserStartedSpeakingFrame

    from realtime_turn import SuppressibleVADUserTurnStartStrategy

    track = _audio_track()
    session = RealtimeSession("log-health-test", channel_server=SimpleNamespace())
    session._connection = SimpleNamespace(audio_input_track=lambda: track)
    strategy = SuppressibleVADUserTurnStartStrategy(is_suppressed=session._turns_suppressed)
    strategy.trigger_user_turn_started = AsyncMock()
    try:
        await session._send_audio_end(False)
        assert not track.is_enabled()
        # Current firmware sends only RTP on wake, no realtime.resume.
        assert isinstance(await track.recv(), AudioFrame)
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        strategy.trigger_user_turn_started.assert_awaited_once()
        session.set_mic_paused(True)
        strategy.trigger_user_turn_started.reset_mock()
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        strategy.trigger_user_turn_started.assert_not_awaited()
        session.set_mic_paused(False)
        await strategy.process_frame(VADUserStartedSpeakingFrame())
        strategy.trigger_user_turn_started.assert_awaited_once()
        assert track.is_enabled()
    finally:
        track.stop()
