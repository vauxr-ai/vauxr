from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("pipecat")
from pipecat.frames.frames import InterruptionFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import RawAudioTrack, SmallWebRTCOutputTransport

from realtime_audio_diagnostics import LiveAudioDiagnostics
from realtime_transcript import TranscriptRelay


def diagnostic() -> tuple[LiveAudioDiagnostics, list[dict[str, object]]]:
    rows: list[dict[str, object]] = []
    relay = TranscriptRelay(AsyncMock(return_value=True), Mock())
    return LiveAudioDiagnostics(relay, emit=rows.append), rows


async def test_provider_silence_is_distinct_from_output_underrun_and_recv_race() -> None:
    diag, _ = diagnostic()
    track = RawAudioTrack(24000)
    original_add, original_recv = track.add_audio_bytes, track.recv
    diag.bind_track(track)
    try:
        # A genuine empty queue generates silence; a provider silent chunk is
        # still queued PCM, despite identical samples and continuous timestamps.
        assert not (await track.recv()).to_ndarray().any()
        silent = bytes(480)
        diag.pcm("provider", silent)
        acknowledged = track.add_audio_bytes(silent)
        assert not acknowledged.done()
        assert not (await track.recv()).to_ndarray().any()
        assert acknowledged.result() is True
        # Enter recv with an empty queue, then enqueue during its timing sleep.
        track._start = time.time() + .03
        receiving = asyncio.create_task(track.recv())
        await asyncio.sleep(.005)
        audible = b"\x00\x40" * 240
        diag.pcm("provider", audible)
        acknowledged = track.add_audio_bytes(audible)
        assert (await receiving).to_ndarray().any()
        assert acknowledged.result() is True
        row = diag.sample()
        assert row["events"]["auto_silence_10ms"] == 1
        assert row["events"]["queued_pcm_10ms"] == 2
        assert row["pcm"]["provider"]["samples"] == 480
        assert row["pcm"]["provider"]["nonzero_samples"] == 240
        assert row["pcm"]["provider"]["rms"] == pytest.approx(.3535534)
        assert row["pcm"]["consumed"]["samples"] == 720
        assert row["track_queue_ms"] == 0
    finally:
        await diag.close()
        assert track.add_audio_bytes == original_add
        assert track.recv == original_recv
        track.stop()


async def test_write_backpressure_cancellation_and_pipeline_interruption_are_observed() -> None:
    diag, _ = diagnostic()
    entered = asyncio.Event()
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    async def write(_frame: OutputAudioRawFrame) -> bool:
        entered.set()
        return await future

    output = SmallWebRTCOutputTransport(SimpleNamespace(), TransportParams(audio_out_enabled=True))
    output.write_audio_frame = write
    process = AsyncMock()
    output.process_frame = process
    diag.bind_output(output)
    writing = asyncio.create_task(output.write_audio_frame(OutputAudioRawFrame(bytes(1920), 24000, 1)))
    await entered.wait()
    row = diag.sample()
    assert row["pcm"]["write"]["samples"] == 960
    assert row["write_inflight_ms"] > 0
    interruption = InterruptionFrame()
    await output.process_frame(interruption, FrameDirection.DOWNSTREAM)
    process.assert_awaited_once_with(interruption, FrameDirection.DOWNSTREAM)
    writing.cancel()
    await asyncio.gather(writing, return_exceptions=True)
    assert future.cancelled()  # Instrumentation must not shield/change pinned behavior.
    row = diag.sample()
    assert row["events"]["pipeline_interruption"] == 1
    assert row["events"]["write_cancelled"] == 1
    await diag.close()
    assert output.write_audio_frame is write
    assert output.process_frame is process


async def test_event_allowlist_resets_windows_and_retains_no_payloads() -> None:
    diag, rows = diagnostic()
    diag.event("session.input_transcript.delta")
    diag.event("response.cancelled", nested=True)
    diag.event("session.close", outgoing=True)
    diag.event("secret-not-an-event")
    diag.pcm("provider", b"\x00\x40" * 240)
    first = diag.sample()
    assert first["events"] == {"rx.session.input_transcript.delta": 1, "nested.response.cancelled": 1,
                               "tx.session.close": 1, "rx.unknown": 1}
    assert first["pcm"]["provider"]["rms"] == .5
    second = diag.sample()
    assert second["events"] == {}
    assert second["pcm"]["provider"]["samples"] == 0
    assert second["pcm"]["provider"]["last_age_ms"] >= 0
    assert "secret" not in json.dumps(first)
    assert not any(isinstance(value, bytes) for value in vars(diag).values())
    await diag.close()
    assert len(rows) == 1
    assert rows[0]["phase"] == "stop"


async def test_sampler_expires_restores_hooks_and_stops_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    diag, rows = diagnostic()
    monkeypatch.setattr(diag, "WINDOWS", 1)
    track = RawAudioTrack(24000)
    original = track.recv
    diag.bind_track(track)
    diag.start()
    await asyncio.wait_for(diag._task, 2)
    assert [row["phase"] for row in rows] == ["start", "sample", "stop"]
    assert not diag.active
    assert track.recv == original
    diag.pcm("provider", bytes(480))
    diag.event("session.started")
    await diag.close()
    assert len(rows) == 3
    track.stop()


async def test_real_live_peer_correlates_provider_output_and_local_silence(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import base64
    import logging

    from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from pipecat.services.openai.live.llm import OpenAILiveLLMService
    from websockets.asyncio.server import ServerConnection, serve

    import agent_registry
    import device_registry
    import realtime_session
    from realtime_session import RealtimeManager

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    monkeypatch.setenv("REALTIME_AUDIO_DIAGNOSTICS", "1")
    monkeypatch.setattr(agent_registry, "get_active", lambda: SimpleNamespace(id="selected", type="openclaw"))
    manager = RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    manager.configure(SimpleNamespace(
        realtime_request=AsyncMock(return_value={"instructions": "", "messages": []})))
    manager._live_devices.add("diagnostic-peer")
    device_registry.register("diagnostic-peer", ws=SimpleNamespace(closed=False, send_str=AsyncMock()))
    handler = manager._request_handler()
    handler.update_ice_servers([])
    remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    remote.addTrack(AudioStreamTrack())
    remote.createDataChannel("chat")
    sent = asyncio.Event()
    decoded_nonzero = asyncio.Event()
    receivers: list[asyncio.Task[None]] = []

    @remote.on("track")
    def on_track(track: AudioStreamTrack) -> None:
        async def receive() -> None:
            while True:
                if (await track.recv()).to_ndarray().any():
                    decoded_nonzero.set()
        receivers.append(asyncio.create_task(receive()))

    async def provider(ws: ServerConnection) -> None:
        async for raw in ws:
            if json.loads(raw)["type"] == "session.start":
                await ws.send(json.dumps({"type": "session.started", "session": {"id": "local"}}))
                # Known nonzero PCM then explicit provider silence; afterward the
                # provider sends nothing and the output track must generate silence.
                for audio in (b"\x00\x40" * 3840, bytes(7680)):
                    await ws.send(json.dumps({"type": "session.output_audio.delta",
                                             "delta": base64.b64encode(audio).decode()}))
                sent.set()

    session = None
    connect = OpenAILiveLLMService._connect
    async with serve(provider, "127.0.0.1", 0) as server:
        async def local_connect(service: OpenAILiveLLMService) -> None:
            service.base_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            await connect(service)
        monkeypatch.setattr(OpenAILiveLLMService, "_connect", local_connect)
        caplog.set_level(logging.INFO, logger="vauxr.live_audio")
        try:
            await remote.setLocalDescription(await remote.createOffer())
            answer = await manager.handle_offer("diagnostic-peer", {
                "sdp": remote.localDescription.sdp, "type": "offer",
            })
            await remote.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            await asyncio.wait_for(sent.wait(), 5)
            await asyncio.wait_for(decoded_nonzero.wait(), 5)
            await asyncio.sleep(1.2)
            session = manager._sessions["diagnostic-peer"]
            await asyncio.wait_for(manager.stop("diagnostic-peer"), 5)
            assert not session._live_service.audio_diagnostics.active
            await session._runner.cancel()
            await asyncio.wait_for(asyncio.shield(session._runner_task), 5)
            rows = [json.loads(record.getMessage().removeprefix("live_audio "))
                    for record in caplog.records if record.name == "vauxr.live_audio"]
            assert rows[0]["phase"] == "start"
            assert rows[-1]["phase"] == "stop"
            assert len({row["trace"] for row in rows}) == 1
            assert sum(row["pcm"]["provider"]["samples"] for row in rows) == 7680
            assert sum(row["pcm"]["write"]["samples"] for row in rows) == 7680
            assert sum(row["events"].get("queued_pcm_10ms", 0) for row in rows) == 32
            assert sum(row["events"].get("auto_silence_10ms", 0) for row in rows) > 0
            assert sum(row["events"].get("rx.session.output_audio.delta", 0) for row in rows) == 2
            assert sum(row["pcm"]["mic"]["samples"] for row in rows) > 0
            assert "diagnostic-peer" not in json.dumps(rows)
            assert "local-test-only" not in json.dumps(rows)
        finally:
            session = manager._sessions.get("diagnostic-peer", session)
            await manager.stop("diagnostic-peer")
            if session is not None and session._runner is not None:
                await session._runner.cancel()
                await asyncio.wait_for(asyncio.shield(session._runner_task), 5)
            await handler.close()
            await remote.close()
            for receiver in receivers:
                receiver.cancel()
            await asyncio.gather(*receivers, return_exceptions=True)
            device_registry.unregister("diagnostic-peer")
