"""Run the installed Pipecat Live service against a local wire-protocol peer, no paid API."""
import asyncio
import base64
import json
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")
from websockets.asyncio.server import serve
from pipecat.frames.frames import InputAudioRawFrame, InterruptionFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import RawAudioTrack, SmallWebRTCClient, SmallWebRTCOutputTransport

from realtime_live import LiveService


@pytest.mark.parametrize('physical', [False, True])
async def test_installed_live_audio_delegation_recording_and_shutdown(monkeypatch, physical):
    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    wire, operations, output_audio = [], [], []
    started, delegated, release, spoken, received_mic, disconnected = (asyncio.Event() for _ in range(6))

    async def request(agent, device, session, operation, payload, timeout):
        assert (agent, device) == ("selected", "browser")
        operations.append((operation, payload))
        if operation == "consult":
            records = [f for op, p in operations if op == "record" for f in p["fragments"]]
            assert records[0]["text"] == "Turn on the lamp"
            delegated.set()
            await release.wait()
            return {"text": "The backend action completed once."}
        return {"recorded": len(payload["fragments"])}

    session = SimpleNamespace(_agent_server=SimpleNamespace(realtime_request=request), device_id="browser",
        _send_control=AsyncMock(return_value=True), _touch_activity=lambda: None, close=AsyncMock(),
        _handoff_pending=physical, _closed=False, _ended_notified=False, _mic_paused=False,
        _owns_control=lambda: True)
    llm = LiveService(session, "selected", {"realtime_model": "gpt-live-1", "realtime_voice": "cedar"})
    session._handoff_pending = False
    if physical:
        llm.device_activity.start()

    # Exercise the installed output queue, client writer and track consumer.
    # Only peer connection setup/RTP are omitted; enqueue is not consumption.
    track = RawAudioTrack(24000, auto_silence=False)
    enqueued, consume = asyncio.Event(), asyncio.Event()
    interruptions = []
    expected_audio = b"\x11\x11" * 960 + bytes(960 * 2) + b"\x22\x22" * 960
    correction_audio = bytes(960 * 2) + b"\x33\x33" * 960
    original_add = track.add_audio_bytes

    def add_audio(data):
        result = original_add(data)
        enqueued.set()
        return result

    track.add_audio_bytes = add_audio
    client = SimpleNamespace(_audio_output_track=track, _can_send=lambda: True,
        setup=AsyncMock(), connect=AsyncMock(), disconnect=AsyncMock(), send_message=AsyncMock())

    async def write(frame):
        return await SmallWebRTCClient.write_audio_frame(client, frame)

    client.write_audio_frame = write
    output = SmallWebRTCOutputTransport(client, TransportParams(audio_out_enabled=True))
    if physical:
        llm.device_activity.bind_output(output)
        llm.device_activity.bind_track(track)

    async def receive():
        await consume.wait()
        while True:
            frame = await track.recv()
            output_audio.append(frame.to_ndarray().tobytes())

    receiver = asyncio.create_task(receive())

    class InterruptionTap(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InterruptionFrame):
                interruptions.append(frame)
            await self.push_frame(frame, direction)

    async def provider(ws):
        try:
            async for raw in ws:
                event = json.loads(raw); wire.append(event)
                if event["type"] == "session.start":
                    assert event["session"]["model"] == "gpt-live-1"
                    assert event["session"]["delegation"]["type"] == "client"
                    await ws.send(json.dumps({"type": "session.started", "session": {"id": "local"}}))
                    for event in [
                        {"type": "session.input_transcript.delta", "delta": "Tu"},
                        {"type": "session.input_transcript.delta", "delta": "rn on"},
                        {"type": "session.input_transcript.delta", "delta": " the lamp"},
                        {"type": "session.output_transcript.delta", "delta": "I will"},
                        {"type": "session.output_transcript.delta", "delta": " check"},
                        {"type": "session.output_transcript.delta", "delta": "."},
                        {"type": "session.output_audio.delta", "delta": base64.b64encode(expected_audio).decode()},
                        {"type": "session.delegation.created", "delegation": {"id": "one", "target": "client"}},
                    ]:
                        await ws.send(json.dumps(event))
                    started.set()
                elif event["type"] == "session.input_audio.append":
                    # Live handles barge-in itself: overlapping speaker deltas,
                    # no synthetic interruption that would cancel backend work.
                    for update in [
                        {"type": "session.output_transcript.delta", "delta": "Working on"},
                        {"type": "session.input_transcript.delta", "delta": "Actually,"},
                        {"type": "session.input_transcript.delta", "delta": " stop."},
                        {"type": "session.output_audio.delta",
                         "delta": base64.b64encode(correction_audio).decode()},
                    ]:
                        await ws.send(json.dumps(update))
                    received_mic.set()
                elif event["type"] == "session.commentary.append":
                    await ws.send(json.dumps({"type": "session.output_transcript.delta", "delta": "Done"}))
                    spoken.set()
        finally:
            disconnected.set()

    async with serve(provider, "127.0.0.1", 0) as server:
        llm.base_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        context = LLMContext(messages=[{"role": "system", "content": "Selected backend profile"}])
        user, assistant = LLMContextAggregatorPair(context)
        worker = PipelineWorker(Pipeline([user, llm, InterruptionTap(), output, assistant]), enable_rtvi=False,
            params=PipelineParams(audio_in_sample_rate=24000, audio_out_sample_rate=24000))
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        running = asyncio.create_task(runner.run())
        try:
            await worker.queue_frame(LLMRunFrame())
            await asyncio.wait_for(started.wait(), 5)
            def transcripts():
                return [call.args[0] for call in session._send_control.call_args_list
                        if call.args[0]["type"] == "realtime.transcript"]

            async def wait_for_controls(predicate):
                async with asyncio.timeout(5):
                    while not predicate(transcripts()):
                        await asyncio.sleep(0.01)

            # Audio is available before the quiet-gap final; snapshots replace
            # one turn even when deltas split words, spaces or punctuation.
            await asyncio.wait_for(enqueued.wait(), 5)
            assert output_audio == [], "transport enqueue must not count as playback consumption"
            consume.set()
            async with asyncio.timeout(5):
                while sum(map(len, output_audio)) < len(expected_audio):
                    await asyncio.sleep(0.01)
            assert b"".join(output_audio) == expected_audio, [(len(c), c[:4]) for c in output_audio]
            assert not any(t["final"] for t in transcripts())
            assert operations == [], "neither record nor consultation waits may block audio"
            await wait_for_controls(lambda ts: len([t for t in ts if t["final"]]) == 2)
            await asyncio.wait_for(delegated.wait(), 5)
            initial = transcripts()
            assert [t["text"] for t in initial if t["final"]] == ["Turn on the lamp", "I will check."]
            for role in ("user", "assistant"):
                assert len({t["turn_id"] for t in initial if t["role"] == role}) == 1
            assert len({t["turn_id"] for t in initial}) == 2
            # Input still flows while provider output and backend work overlap.
            await worker.queue_frame(InputAudioRawFrame(bytes(960), 24000, 1))
            await asyncio.wait_for(received_mic.wait(), 5)
            assert output_audio
            assert operations[0][0] == "record"
            consultation = next(payload for op, payload in operations if op == "consult")
            assert "Turn on the lamp" not in consultation["request"], "do not replay transcript as new prompt"
            assert not spoken.is_set()
            await wait_for_controls(lambda ts: len([t for t in ts if t["final"]]) == 4)
            finals = [t for t in transcripts() if t["final"]]
            assert [t["text"] for t in finals] == [
                "Turn on the lamp", "I will check.", "Working on", "Actually, stop.",
            ]
            assert len({t["turn_id"] for t in finals}) == 4
            assert not spoken.is_set(), "barge-in must not cancel or replay the backend action"
            assert interruptions == [], "Live's provider-owned barge-in must not clear queued output"
            async with asyncio.timeout(5):
                while sum(map(len, output_audio)) < len(expected_audio + correction_audio):
                    await asyncio.sleep(0.01)
            assert b"".join(output_audio) == expected_audio + correction_audio
            release.set()
            await asyncio.wait_for(spoken.wait(), 5)
            await wait_for_controls(lambda ts: any(t["text"] == "Done" for t in ts))
            if physical:
                controls = [c.args[0]['type'] for c in session._send_control.call_args_list]
                assert 'audio.start' in controls
                assert llm.device_activity.ready
            else:
                assert llm.device_activity is None
            # Stop mid-turn: seal the partial speaker turn once without waiting
            # for a provider final or recording cumulative UI snapshots.
            await llm.finish_transcript()
            recorded = [f for op, payload in operations if op == "record" for f in payload["fragments"]]
            assert [f["text"] for f in recorded] == [
                "Turn on the lamp", "I will check.", "Working on", "Actually, stop.", "Done",
            ]
            assert len({f["id"] for f in recorded}) == len(recorded)
            assert [f["id"] for f in recorded] == [t["turn_id"] for t in transcripts() if t["final"]]
            assert sum(t["final"] and t["text"] == "Done" for t in transcripts()) == 1
            await llm.finish_transcript()
            assert len([f for op, p in operations if op == "record" for f in p["fragments"]]) == 5
            assert len([op for op, _ in operations if op == "consult"]) == 1
            assert any(not f["delivered"] for op, payload in operations if op == "record" for f in payload["fragments"] if f["role"] == "assistant")
        finally:
            release.set()
            if llm.flush_task:
                llm.flush_task.cancel()
            await runner.cancel()
            await asyncio.wait_for(running, 5)
            await asyncio.wait_for(disconnected.wait(), 5)
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            track.stop()
        assert llm._websocket is None


async def test_record_retry_preserves_turn_ids_and_serializes_flushes(monkeypatch):
    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    written, calls = {}, []
    entered, release = asyncio.Event(), asyncio.Event()

    async def request(agent, device, session, operation, payload, timeout):
        assert operation == "record"
        calls.append(payload["fragments"])
        for turn in payload["fragments"]:
            written.setdefault(turn["id"], turn["text"])
        if len(calls) == 1:
            raise ConnectionError("record committed but acknowledgement lost")
        entered.set()
        await release.wait()
        return {"recorded": len(payload["fragments"])}

    session = SimpleNamespace(_agent_server=SimpleNamespace(realtime_request=request), device_id="browser")
    llm = LiveService(session, "selected", {"realtime_model": "gpt-live-1", "realtime_voice": "cedar"})
    first = {"id": "user-turn", "role": "user", "text": "Turn on the lamp", "delivered": False}
    llm.fragments.append(first)
    with pytest.raises(ConnectionError):
        await llm.flush_transcript()
    assert llm.fragments == [first]
    retry = asyncio.create_task(llm.flush_transcript())
    await asyncio.wait_for(entered.wait(), 1)
    llm.fragments.append({"id": "partial-turn", "role": "assistant", "text": "Working on", "delivered": False})
    concurrent = asyncio.create_task(llm.flush_transcript())
    release.set()
    await asyncio.gather(retry, concurrent)
    assert calls[0] == calls[1] == [first]
    assert len(calls) == 3
    assert written == {"user-turn": "Turn on the lamp", "partial-turn": "Working on"}
    assert llm.fragments == []


@pytest.mark.parametrize("failure", ["lost_ack", "invalid_context"])
async def test_failed_bootstrap_releases_scope_through_real_offer_cleanup(monkeypatch, failure):
    from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection

    import agent_registry
    import realtime_session
    from realtime_session import RealtimeManager

    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    monkeypatch.setattr(agent_registry, "get_active", lambda: SimpleNamespace(id="selected", type="openclaw"))
    manager = RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    scopes, operations = set(), []

    async def request(agent, device, scope, operation, payload, timeout):
        # Only the remote plugin boundary is replaced. Keep real LiveService,
        # session startup, offer error handling and teardown (including release).
        assert (agent, device) == ("selected", "browser")
        operations.append((operation, scope))
        if operation == "bootstrap":
            scopes.add(scope)
            if failure == "lost_ack":
                raise ConnectionError("scope created but bootstrap acknowledgement lost")
            return {"instructions": 42, "messages": []}
        assert operation == "release"
        scopes.remove(scope)
        return {}

    manager.configure(SimpleNamespace(realtime_request=request))
    manager._live_devices.add("browser")
    handler = manager._request_handler()
    handler.update_ice_servers([])
    remote = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    remote.addTrack(AudioStreamTrack())
    try:
        await remote.setLocalDescription(await remote.createOffer())
        # Pipecat logs callback failures rather than propagating them.
        await asyncio.wait_for(manager.handle_offer("browser", {
            "sdp": remote.localDescription.sdp, "type": "offer",
        }), 3)
        assert [op for op, _ in operations] == ["bootstrap", "release"]
        assert operations[0][1] == operations[1][1]
        assert not scopes
        assert not manager._sessions
        await manager.stop("browser")
        await handler.close()
        assert len(operations) == 2
        assert not manager.can_accept_offer("browser")
    finally:
        await asyncio.wait_for(handler.close(), 2)
        await asyncio.wait_for(remote.close(), 2)


async def test_device_handoff_bootstraps_completed_action_without_speaking_again(monkeypatch):
    """Exercise the existing bootstrap contract and pinned session.start encoding."""
    import agent_registry
    import realtime_live
    import realtime_session
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    monkeypatch.setattr(agent_registry, "get_active", lambda: SimpleNamespace(id="selected", type="openclaw"))
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "_manager", manager)
    operations, wire = [], []
    completed = [
        {"role": "user", "content": "Switch on the lamp"},
        {"role": "assistant", "content": "The lamp is on."},
        {"role": "developer", "content": "Action lamp-42 completed successfully. Do not repeat it."},
    ]

    async def request(agent, device, scope, operation, payload, timeout):
        operations.append(operation)
        assert (agent, device) == ("selected", "device")
        assert operation == "bootstrap"
        return {"instructions": "Use the completed backend action state.", "messages": completed}

    # Keep the actual Live service, context adapter and event encoding. No paid
    # provider, RTP negotiation or running pipeline is needed for this wire probe.
    blocked = asyncio.Event()
    monkeypatch.setattr(realtime_live, "WorkerRunner", lambda **kwargs: SimpleNamespace(
        add_workers=AsyncMock(), run=blocked.wait))
    session = realtime_session.RealtimeSession("device", SimpleNamespace(realtime_request=request))
    manager.prepare_handoff("device")
    session._handoff_pending = True
    connection = SmallWebRTCConnection(ice_servers=[])
    try:
        await session.start(connection)
        service = session._live_service
        async def capture(event):
            wire.append(event.model_dump(exclude_none=True))
        service.send_client_event = capture
        await service._handle_context(session._context)
        assert operations == ["bootstrap"]
        assert len(wire) == 1 and wire[0]["type"] == "session.start"
        encoded = json.dumps(wire[0])
        assert "Switch on the lamp" in encoded and "The lamp is on." in encoded
        assert "lamp-42 completed successfully" in encoded
        assert not service._opening_instruction
        assert completed[-1]["role"] == "developer"  # Do not mutate backend records.
        # Before media acknowledgement the pinned service receives no microphone PCM.
        parent_process = AsyncMock()
        monkeypatch.setattr(realtime_live.OpenAILiveLLMService, "process_frame", parent_process)
        from pipecat.processors.frame_processor import FrameDirection
        frame = InputAudioRawFrame(bytes(960), 24000, 1)
        await service.process_frame(frame, FrameDirection.DOWNSTREAM)
        parent_process.assert_not_awaited()
        session._handoff_pending = False
        await service.process_frame(frame, FrameDirection.DOWNSTREAM)
        parent_process.assert_awaited_once()
    finally:
        for task in (session._runner_task, session._backstop_task):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if session._live_service:
            await session._live_service.cleanup()
        await connection.disconnect()
