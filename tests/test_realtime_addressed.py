"""Physical follow-up admission using installed Pipecat and its real output track."""
import asyncio
import base64
import json
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import (
    RawAudioTrack,
    SmallWebRTCClient,
    SmallWebRTCOutputTransport,
)
from pipecat.workers.runner import WorkerRunner
from websockets.asyncio.server import serve

from vauxr.realtime import addressed
from vauxr.realtime.addressed import AddressGate
from vauxr.realtime.device_activity import DeviceActivity
from vauxr.realtime.live import LiveService


@pytest.mark.parametrize("outcome", ["addressed", "ambient", "failure", "dismissal"])
async def test_physical_output_and_backend_are_held_until_addressed(monkeypatch, outcome):
    assert version("pipecat-ai") == "1.9.0"
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    checking, answer, provider_sent, delegated = (asyncio.Event() for _ in range(4))
    operations, played = [], []
    first_played, next_turn = asyncio.Event(), asyncio.Event()
    classifications = []

    async def classify(text, previous):
        classifications.append((text, previous))
        if len(classifications) == 1:
            assert text == "Hello Vauxr"
            return True
        checking.set()
        await answer.wait()
        if outcome == "failure":
            raise TimeoutError()
        return outcome == "addressed"

    monkeypatch.setattr(addressed, "classify_address", classify)

    async def request(agent, device, scope, operation, payload, timeout):
        operations.append(operation)
        if operation == "consult":
            delegated.set()
        return {"text": "The lamp is on."}

    session = SimpleNamespace(is_physical_live=True, _startup=None, _closed=False,
        _ended_notified=False, _mic_paused=False, _owns_control=lambda: True,
        _send_control=AsyncMock(return_value=True), _touch_activity=lambda: None,
        close=AsyncMock(), device_id="speaker", _agent_server=SimpleNamespace(realtime_request=request))
    live = LiveService(session, "selected", {"realtime_model": "gpt-live-1", "realtime_voice": "cedar"})
    track = RawAudioTrack(24000, auto_silence=False)
    client = SimpleNamespace(_audio_output_track=track, _can_send=lambda: True,
        setup=AsyncMock(), connect=AsyncMock(), disconnect=AsyncMock(), send_message=AsyncMock())

    async def write(frame):
        return await SmallWebRTCClient.write_audio_frame(client, frame)

    client.write_audio_frame = write
    output = SmallWebRTCOutputTransport(client, TransportParams(audio_out_enabled=True))
    live.device_activity.bind_output(output)
    live.device_activity.bind_track(track)
    live.device_activity.start()
    pcm = b"\x11\x11" * 960
    silence = bytes(len(pcm))

    async def consume():
        while True:
            played.append((await track.recv()).to_ndarray().tobytes())
            first_played.set()

    async def provider(ws):
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] == "session.start":
                await ws.send(json.dumps({"type": "session.started", "session": {"id": "local"}}))
                # Begin with a wake-admitted conversation, then exercise the
                # active follow-up without another wake or reconnect.
                for initial in [
                    {"type": "session.input_transcript.delta", "delta": "Hello Vauxr"},
                    {"type": "session.output_transcript.delta", "delta": "Hello."},
                    {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode()},
                ]:
                    await ws.send(json.dumps(initial))
                await next_turn.wait()
                text = {"addressed": "Turn on the lamp", "ambient": "Alex, the meeting starts at noon",
                        "failure": "Turn on the lamp", "dismissal": "Not talking to you"}[outcome]
                # Split the dismissal to exercise accumulated transcript matching.
                for part in [text[:5], text[5:]]:
                    await ws.send(json.dumps({"type": "session.input_transcript.delta", "delta": part}))
                for update in [
                    {"type": "session.output_transcript.delta", "delta": "Checking the lamp."},
                    {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode()},
                    {"type": "session.output_audio.delta", "delta": base64.b64encode(silence).decode()},
                    {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm).decode()},
                    {"type": "session.delegation.created", "delegation": {"id": "work", "target": "client"}},
                ]:
                    await ws.send(json.dumps(update))
                provider_sent.set()

    user, assistant = LLMContextAggregatorPair(LLMContext())
    worker = PipelineWorker(Pipeline([user, live, output, assistant]), enable_rtvi=False)
    runner = WorkerRunner(handle_sigint=False)
    receiver = asyncio.create_task(consume())
    async with serve(provider, "127.0.0.1", 0) as server:
        live.base_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        await runner.add_workers(worker)
        running = asyncio.create_task(runner.run())
        try:
            await worker.queue_frame(LLMRunFrame())
            await asyncio.wait_for(first_played.wait(), 3)
            async with asyncio.timeout(3):
                while not any(c.args[0] == {"type": "audio.end", "follow_up": True}
                              for c in session._send_control.call_args_list):
                    await asyncio.sleep(.01)
            # Both output consumption and audio.end prove active follow-up.
            played.clear()
            session._send_control.reset_mock()
            next_turn.set()
            await asyncio.wait_for(provider_sent.wait(), 3)
            if outcome != "dismissal":
                await asyncio.wait_for(checking.wait(), 3)
                assert not played
                assert "consult" not in operations
                assert not any(c.args[0].get("text") == "Checking the lamp."
                               for c in session._send_control.call_args_list)
                answer.set()
            if outcome == "addressed":
                await asyncio.wait_for(delegated.wait(), 3)
                async with asyncio.timeout(3):
                    while not played:
                        await asyncio.sleep(.01)
                async with asyncio.timeout(3):
                    while sum(map(len, played)) < len(pcm + silence + pcm):
                        await asyncio.sleep(.01)
                assert b"".join(played) == pcm + silence + pcm
                assert not live.address_gate.closed
            else:
                async with asyncio.timeout(3):
                    while not session.close.called:
                        await asyncio.sleep(.01)
                # Late provider output cannot reopen output or follow-up.
                from pipecat.services.openai.live import events
                await live._handle_server_event(events.OutputAudioDeltaEvent(
                    type="session.output_audio.delta", delta=base64.b64encode(pcm).decode()))
                await live.device_activity.tick()
                assert not played
                assert "consult" not in operations
                controls = [c.args[0] for c in session._send_control.call_args_list
                            if c.args[0]["type"] in ("audio.start", "audio.end", "speech.start")]
                assert controls[-1] == {"type": "audio.end", "follow_up": False}
                assert live.address_gate.closed
                if outcome == "dismissal":
                    assert not checking.is_set()
        finally:
            answer.set()
            next_turn.set()
            if live.flush_task:
                live.flush_task.cancel()
            await live.finish_transcript()
            await runner.cancel()
            await asyncio.wait_for(running, 5)
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
            track.stop()


async def test_late_classifier_cannot_admit_new_turn(monkeypatch):
    checking, release = asyncio.Event(), asyncio.Event()

    async def classify(*args):
        checking.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # Simulate a request completing after cancellation.
        return True

    monkeypatch.setattr(addressed, "classify_address", classify)
    gate = AddressGate(AsyncMock())
    gate.begin()
    old = object()
    await gate.hold(old, 10)
    emit = AsyncMock()
    gate.decide("Turn on the lamp", emit)
    task = gate.task
    await checking.wait()
    gate.begin()
    release.set()
    await task
    assert not gate.allowed
    emit.assert_not_awaited()
    assert not gate.pending
    await gate.stop()


async def test_output_bound_and_dismissal_wake_waiters():
    quiet = AsyncMock()
    gate = AddressGate(quiet)
    waiter = asyncio.create_task(gate.admitted())
    await gate.hold(object(), gate.MAX_OUTPUT + 1)
    assert not await waiter
    assert gate.pending == []
    await gate.stop()
    quiet.assert_awaited_once()


@pytest.mark.parametrize("text", ["Not talking to you.", "We're not talking to you", "Give us space, please."])
def test_explicit_dismissals(text):
    assert addressed.dismissed(text)


async def test_quiet_control_wins_over_inflight_followup():
    entered, release = asyncio.Event(), asyncio.Event()
    controls = []

    async def send(message):
        if message.get("follow_up") is True:
            entered.set()
            await release.wait()
        controls.append(message)
        return True

    session = SimpleNamespace(_closed=False, _ended_notified=False, _mic_paused=False,
        _owns_control=lambda: True, _send_control=send, _touch_activity=lambda: None, close=AsyncMock())
    activity = DeviceActivity(session)
    activity.ready = True
    tick = asyncio.create_task(activity.tick())
    await entered.wait()
    stop = asyncio.create_task(activity.stop())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(tick, stop)
    await activity.tick()
    assert controls == [{"type": "audio.end", "follow_up": True},
                        {"type": "audio.end", "follow_up": False}]


@pytest.mark.parametrize("addressed_value", [True, False])
async def test_classifier_uses_installed_responses_schema_without_tools(monkeypatch, addressed_value):
    import httpx
    from openai import AsyncOpenAI

    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            "id": "resp_local", "object": "response", "created_at": 1,
            "model": "gpt-4.1-mini-2025-04-14", "status": "completed",
            "output": [{"type": "message", "id": "msg_local", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "annotations": [],
                                     "text": json.dumps({"addressed": addressed_value})}]}],
        })

    def client(**kwargs):
        return AsyncOpenAI(api_key="local-test-only", base_url="https://local.invalid/v1",
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), **kwargs)

    monkeypatch.setattr(addressed, "AsyncOpenAI", client)
    assert await addressed.classify_address("Yes, please", "Would you like the forecast?") is addressed_value
    request = requests[0]
    assert not request.get("tools")
    assert request["store"] is False
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"]["properties"]["addressed"]["type"] == "boolean"
    assert json.loads(request["input"]) == {
        "previous_assistant_reply": "Would you like the forecast?", "latest_speech": "Yes, please"}


async def test_classifier_timeout_fails_closed(monkeypatch):
    async def classify(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(addressed, "classify_address", classify)
    quiet = AsyncMock()
    gate = AddressGate(quiet)
    gate.begin()
    gate.decide("Turn on the lamp", AsyncMock())
    assert not await asyncio.wait_for(gate.admitted(), 4)
    quiet.assert_awaited_once()
