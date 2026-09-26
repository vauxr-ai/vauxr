"""Production WS dispatcher -> real RTP/Pipecat -> local GPT-Live protocol peer."""
import asyncio
import base64
import json
import numpy as np
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from pipecat.audio.resamplers.soxr_resampler import SOXRAudioResampler
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.services.openai.live.llm import OpenAILiveLLMService
from websockets.asyncio.server import serve

import vauxr.agents.registry as agent_registry
import vauxr.config as config
import vauxr.devices.registry as registry
import vauxr.realtime.live as realtime_live
import vauxr.realtime.session as realtime_session
import vauxr.server as server
from vauxr.auth.policy import Role
from vauxr.realtime.startup import StartupAudio
from vauxr.speech.store import get_store
from tests.auth_helpers import seed


class SequencedTrack(AudioStreamTrack):
    """Changing tones make reordered/duplicated RTP observable after Opus decode."""
    def __init__(self):
        super().__init__()
        self.sequence = 0

    async def recv(self):
        frame = await super().recv()
        self.sequence += 1
        samples = np.arange(frame.samples) + frame.pts
        tone = (5000 * np.sin(samples * 2 * np.pi * (220 + self.sequence * 13) / frame.sample_rate))
        frame.planes[0].update(tone.astype("<i2").tobytes())
        return frame


async def until(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.fixture
async def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REALTIME_ENABLED", "1")
    monkeypatch.setenv("REALTIME_HOST", "127.0.0.1")
    monkeypatch.setenv("REALTIME_STUN_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    config.reset_config()
    registry.reset()
    seed("startup-device-test", Role.DEVICE, "startup-test")
    seed("startup-agent-test", Role.INTEGRATION, "selected")
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    app = server.make_app()
    monkeypatch.setattr(agent_registry, "get_active", lambda: SimpleNamespace(id="selected", type="openclaw"))
    registry.update_config("startup-test", {"pipeline_mode": "realtime"})
    get_store().update({"mode": "realtime"}, "startup-test")
    backend = AsyncMock(return_value={"instructions": "", "messages": []})
    manager.configure(SimpleNamespace(realtime_request=backend))
    standard = AsyncMock(side_effect=AssertionError("cold realtime called Standard"))
    monkeypatch.setattr(server, "run_voice_turn", standard)
    async with TestClient(TestServer(app)) as http:
        async with http.ws_connect("/ws") as ws:
            await ws.send_json({"type": "hello", "device_id": "startup-test",
                "token": "startup-device-test", "caps": ["webrtc"], "platform": "voicepe"})
            hello = await ws.receive_json(timeout=2)
            assert hello["realtime"]["startup"] == "ws_pcm_v1"
            assert "handoff" not in hello["realtime"]
            yield SimpleNamespace(manager=manager, http=http, ws=ws, backend=backend, standard=standard)
    await manager.stop_all()
    if manager._handler:
        await manager._handler.close()
    registry.reset()
    config.reset_config()


async def wake(env, number):
    await env.ws.send_json({"type": "realtime.start", "startup_id": number})
    while True:
        message = await env.ws.receive_json(timeout=2)
        if message["type"] == "ready":
            break
        assert message["type"] in {"audio.end", "realtime.ready", "vauxr.speech.store.start"}
    return env.manager._startups["startup-test"]


async def pcm(env, seq, data):
    await env.ws.send_bytes(b"\x01" + seq.to_bytes(2, "big") + data)


@pytest.mark.parametrize("provider_first", [False, True])
async def test_cold_and_later_wake_exact_ws_then_rtp_provider_order(env, monkeypatch, provider_first):
    requested, release = asyncio.Event(), asyncio.Event()
    appended, rtp = [], []

    async def provider(socket):
        async for raw in socket:
            event = json.loads(raw)
            if event["type"] == "session.start":
                requested.set()
                await release.wait()
                await socket.send(json.dumps({"type": "session.started", "session": {"id": "local"}}))
            elif event["type"] == "session.input_audio.append":
                appended.append(base64.b64decode(event["audio"]))

    original_connect = OpenAILiveLLMService._connect
    original_frame = realtime_live.LiveService.process_frame

    async def observe(service, frame, direction):
        if isinstance(frame, InputAudioRawFrame):
            rtp.append(frame.audio)
        await original_frame(service, frame, direction)

    monkeypatch.setattr(realtime_live.LiveService, "process_frame", observe)
    async with serve(provider, "127.0.0.1", 0) as peer:
        async def connect(service):
            service.base_url = f"ws://127.0.0.1:{peer.sockets[0].getsockname()[1]}"
            await original_connect(service)
        monkeypatch.setattr(OpenAILiveLLMService, "_connect", connect)
        for number in (1, 2):
            requested.clear()
            release.clear()
            appended.clear()
            rtp.clear()
            startup = await wake(env, number)
            opening = b"\x13\x05" * 512 + b"\x27\x09" * 512
            await pcm(env, 0, opening[:1024])
            await pcm(env, 1, opening[1024:])
            await until(lambda: startup.next_seq == 2)
            # Duplicate start is idempotent even before there is a peer.
            await env.ws.send_json({"type": "realtime.start", "startup_id": number})
            assert (await env.ws.receive_json(timeout=2))["type"] == "ready"
            assert env.manager._startups["startup-test"] is startup
            remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            remote.addTrack(SequencedTrack())
            session = None
            try:
                await remote.setLocalDescription(await remote.createOffer())
                body = {"device_id": "startup-test", "token": "startup-device-test",
                        "type": "offer", "sdp": remote.localDescription.sdp, "startup_id": number}
                response = await env.http.post("/api/offer", json=body)
                assert response.status == 200
                answer = await response.json()
                session = env.manager._sessions["startup-test"]
                await remote.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
                await asyncio.wait_for(requested.wait(), 5)
                await until(lambda: len(rtp) >= 3)
                assert not appended
                # An overlapping offer must neither create another scope nor stop this one.
                duplicate = await env.http.post("/api/offer", json=body)
                assert duplicate.status == 409
                assert env.manager._sessions["startup-test"] is session
                if provider_first:
                    release.set()
                    await until(lambda: startup.provider_ready)
                    assert not appended
                for invalid in (None, True, number + 100):
                    await env.ws.send_json({"type": "realtime.media_ready", "startup_id": invalid, "next_seq": 2})
                await asyncio.sleep(.05)
                assert not startup.complete and not appended
                await env.ws.send_json({"type": "realtime.media_ready", "startup_id": number, "next_seq": 2})
                await until(lambda: startup.complete)
                if not provider_first:
                    assert not appended
                    release.set()
                await until(lambda: startup.drained and len(appended) >= 8)
                expected_ws = await SOXRAudioResampler().resample(opening, 16000, 24000)
                wire = b"".join(appended)
                expected = expected_ws + b"".join(rtp)
                assert len(wire) > len(expected_ws) + 3 * 960
                # SoX integer output dithers by one LSB on each conversion.
                assert np.max(np.abs(np.frombuffer(wire[:len(expected_ws)], dtype="<i2").astype(int)
                                     - np.frombuffer(expected_ws, dtype="<i2").astype(int))) <= 2
                assert wire[len(expected_ws):] == expected[len(expected_ws):len(wire)]
                assert len(set(rtp)) >= 3
                assert sum(c.kwargs.get("timeout", 0) >= 0 and c.args[3] == "bootstrap"
                           for c in env.backend.await_args_list) == number
                env.standard.assert_not_called()
            finally:
                release.set()
                await env.ws.send_json({"type": "realtime.stop"})
                await until(lambda: "startup-test" not in env.manager._sessions)
                if session and session._runner:
                    # Pipecat 1.9.0's direct PipelineWorker.cancel does not
                    # propagate to its idle child. Reap the local test backend;
                    # active-session shutdown policy is outside this change.
                    await session._live_service._delegation.backend.stop()
                    await session._runner.cancel()
                    await asyncio.wait_for(asyncio.shield(session._runner_task), 5)
                await remote.close()
            assert startup.closed and not startup._ws and not startup._rtp


@pytest.mark.parametrize("failure", ["abort", "timeout", "overflow", "sequence", "bad_marker"])
async def test_startup_failure_discards_and_next_wake_is_clean(env, monkeypatch, failure):
    if failure == "timeout":
        monkeypatch.setattr(StartupAudio, "TIMEOUT_SECONDS", .1)
    startup = await wake(env, 1)
    await pcm(env, 0, b"\x01\x00" * 512)
    await until(lambda: startup.next_seq == 1)
    if failure == "abort":
        await env.ws.send_json({"type": "abort"})
    elif failure == "overflow":
        await pcm(env, 1, bytes(StartupAudio.MAX_AUDIO_SECONDS * 32000))
    elif failure == "sequence":
        await pcm(env, 3, bytes(1024))
    elif failure == "bad_marker":
        await env.ws.send_json({"type": "realtime.media_ready", "startup_id": 1, "next_seq": 2})
    if failure != "abort":
        error = await env.ws.receive_json(timeout=2)
        assert error["type"] == "error"
        assert error["code"].startswith("REALTIME_STARTUP_")
    await until(lambda: not env.manager.can_accept_offer("startup-test"))
    assert startup.closed and not startup._ws and not startup._rtp
    monkeypatch.setattr(StartupAudio, "TIMEOUT_SECONDS", 20)
    retry = await wake(env, 2)
    assert retry is not startup and retry.next_seq == 0
    await env.ws.send_json({"type": "realtime.media_ready", "startup_id": 1, "next_seq": 1})
    await pcm(env, 0, b"\x02\x00" * 512)
    await until(lambda: retry.next_seq == 1)
    assert not retry.complete and bytes(retry._ws) == b"\x02\x00" * 512
    env.standard.assert_not_called()


@pytest.mark.parametrize("cancel_bootstrap", [False, True])
async def test_bootstrap_failure_or_cancellation_cannot_create_late_pipeline(env, cancel_bootstrap):
    entered, release = asyncio.Event(), asyncio.Event()
    operations = []

    async def backend(agent, device, scope, operation, payload, timeout):
        operations.append(operation)
        if operation == "bootstrap":
            entered.set()
            await release.wait()
            if not cancel_bootstrap:
                raise ConnectionError("local bootstrap failure")
            return {"instructions": "", "messages": []}
        return {}

    env.backend.side_effect = backend
    startup = await wake(env, 1)
    await pcm(env, 0, bytes(1024))
    remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    remote.addTrack(AudioStreamTrack())
    pending = None
    try:
        await remote.setLocalDescription(await remote.createOffer())
        pending = asyncio.create_task(env.http.post("/api/offer", json={
            "device_id": "startup-test", "token": "startup-device-test", "startup_id": 1,
            "sdp": remote.localDescription.sdp, "type": "offer"}))
        await asyncio.wait_for(entered.wait(), 2)
        old_session = env.manager._sessions["startup-test"]
        if cancel_bootstrap:
            await env.ws.send_json({"type": "abort"})
            await until(lambda: not env.manager.can_accept_offer("startup-test"))
            replacement = await wake(env, 2)
            await pcm(env, 0, b"\x07\x00" * 512)
            await until(lambda: replacement.next_seq == 1)
        release.set()
        response = await asyncio.wait_for(pending, 3)
        assert response.status in (409, 500)
        assert old_session._task is None and old_session._runner_task is None
        assert startup.closed and not startup._ws and not startup._rtp
        assert operations.count("bootstrap") == operations.count("release") == 1
        assert not env.manager._handler._pcs_map
        if cancel_bootstrap:
            assert env.manager._startups["startup-test"] is replacement
            assert not replacement.closed and replacement.next_seq == 1
            assert registry.get("startup-test").state == "listening"
        else:
            await until(lambda: not env.manager.can_accept_offer("startup-test"))
    finally:
        release.set()
        if pending:
            await asyncio.gather(pending, return_exceptions=True)
        await remote.close()


async def test_provider_rejection_discards_pre_ready_pcm_and_allows_retry(env, monkeypatch):
    requested, reject = asyncio.Event(), asyncio.Event()
    appended = []

    async def provider(socket):
        async for raw in socket:
            event = json.loads(raw)
            if event["type"] == "session.start":
                requested.set()
                await reject.wait()
                await socket.send(json.dumps({"type": "error", "error": {
                    "type": "invalid_request_error", "code": "local_rejection", "message": "Local test rejection"}}))
            elif event["type"] == "session.input_audio.append":
                appended.append(event)

    original = OpenAILiveLLMService._connect
    startup = await wake(env, 1)
    await pcm(env, 0, bytes(1024))
    remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    remote.addTrack(AudioStreamTrack())
    session = None
    async with serve(provider, "127.0.0.1", 0) as peer:
        async def connect(service):
            service.base_url = f"ws://127.0.0.1:{peer.sockets[0].getsockname()[1]}"
            await original(service)
        monkeypatch.setattr(OpenAILiveLLMService, "_connect", connect)
        try:
            await remote.setLocalDescription(await remote.createOffer())
            response = await env.http.post("/api/offer", json={
                "device_id": "startup-test", "token": "startup-device-test", "startup_id": 1,
                "sdp": remote.localDescription.sdp, "type": "offer"})
            assert response.status == 200
            answer = await response.json()
            session = env.manager._sessions["startup-test"]
            await remote.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            await asyncio.wait_for(requested.wait(), 3)
            await until(lambda: len(startup._rtp) >= 2)
            await env.ws.send_json({"type": "realtime.media_ready", "startup_id": 1, "next_seq": 1})
            await until(lambda: startup.complete)
            reject.set()
            await until(lambda: startup.closed and "startup-test" not in env.manager._sessions)
            assert not startup._ws and not startup._rtp and not appended
            # Consume only failure controls before initiating the retry.
            await env.ws.send_json({"type": "realtime.start", "startup_id": 2})
            async with asyncio.timeout(2):
                while (await env.ws.receive_json())["type"] != "ready":
                    pass
            replacement = env.manager._startups["startup-test"]
            assert replacement.id == 2 and not replacement.closed and replacement.next_seq == 0
        finally:
            reject.set()
            await env.manager.stop("startup-test")
            if session and session._runner:
                await session._live_service._delegation.backend.stop()
                await session._runner.cancel()
                await asyncio.wait_for(asyncio.shield(session._runner_task), 5)
            await remote.close()
