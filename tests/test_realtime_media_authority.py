"""Exact media ownership through real Pipecat TTS and output serialization."""

import asyncio
import json
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pipecat")

from aiohttp import WSMsgType
from pipecat.frames.frames import LLMContextFrame, LLMFullResponseEndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import RawAudioTrack

import auth_connections
import channel_registry
import device_registry
import realtime_llm
import realtime_session
import realtime_wyoming
from channel_server import ChannelServer, _Connection
from lifecycle import Lifecycle
from realtime_llm import ChannelLLMService, OutputDrainTap
from realtime_transport import AudioConsumption
from tests.test_integration import ORIGIN, ack_body, deliver, env, setup

assert env and setup  # imported enrollment fixtures


async def test_raw_audio_future_requires_last_recv():
    track = RawAudioTrack(24000, auto_silence=False)
    try:
        consumed = track.add_audio_bytes(b"\0\0" * 480)
        assert isinstance(consumed, asyncio.Future)
        await asyncio.sleep(0)
        assert not consumed.done()
        await track.recv()
        assert not consumed.done()  # The first 10 ms cannot acknowledge 20 ms.
        await track.recv()
        assert consumed.done()
        assert consumed.result() is True
    finally:
        track.stop()


@pytest.mark.parametrize("response", ["slow", "partial_error", "playing", "playing_error", "empty"])
@pytest.mark.parametrize("finish", ["revoke", "drain", "retry"])
async def test_disconnected_media_authority(env, monkeypatch, response, finish):
    service, owner = env
    server = ChannelServer()
    manager = realtime_session.RealtimeManager()
    monkeypatch.setattr(realtime_session, "get_manager", lambda: manager)
    monkeypatch.setattr(realtime_session, "_BOT_IDLE_DEBOUNCE_S", 0)
    monkeypatch.setattr(realtime_llm, "get_segmentation", lambda _: SimpleNamespace(sentence=True))
    body, _, issued = deliver(env)
    service.execute("ack", ack_body(body, issued))
    authenticated, disconnected = asyncio.Event(), asyncio.Event()

    class Socket:
        closed = False

        async def send_str(self, text):
            authenticated.set()

        async def close(self):
            self.closed = True
            disconnected.set()

        async def __aiter__(self):
            yield SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps({
                "type": "channel.auth", "token": issued["credential"]}))
            await disconnected.wait()

    socket = Socket()
    socket_task = asyncio.create_task(server.handle_connection(socket))
    await asyncio.wait_for(authenticated.wait(), 2)
    a = server._connections[issued["channel_id"]]
    b_body, _, b_issued = deliver(env, 2)
    service.execute("ack", ack_body(b_body, b_issued))
    b = _Connection(SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock()))
    await server._handle_auth(b, b_issued["credential"])
    channel_registry._set_active_for_tests(b.channel)  # fallback after A revoke
    assert channel_registry.activate(a.channel.id)
    old = realtime_session.RealtimeSession("speaker", server)
    replacement = realtime_session.RealtimeSession("speaker", server)
    manager._sessions["speaker"] = old
    ws = SimpleNamespace(closed=False, send_str=AsyncMock())
    device_registry.register("speaker", ws=ws)
    old._connection = SimpleNamespace(disconnect=AsyncMock(), audio_input_track=lambda: None)
    replacement._connection = SimpleNamespace(disconnect=AsyncMock(), audio_input_track=lambda: None)
    ready, synthesis, release_tts = asyncio.Event(), asyncio.Event(), asyncio.Event()
    playing, release_output, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    ends, output_completions = [], []
    track = RawAudioTrack(24000, auto_silence=False)
    assert version("pipecat-ai") == "1.9.0"
    original_add = server.add_response_listener

    def add(device, listener):
        original_add(device, listener)
        ready.set()

    monkeypatch.setattr(server, "add_response_listener", add)

    async def synthesize(*args, **kwargs):
        synthesis.set()
        await release_tts.wait()
        yield b"\0\0" * 4800

    monkeypatch.setattr(realtime_wyoming, "synthesize", synthesize)

    class Output(BaseOutputTransport):
        async def start(self, frame):
            await super().start(frame)
            await self.set_transport_ready(frame)

        async def write_audio_frame(self, frame):
            old._on_bot_started_speaking()
            playing.set()
            result = await consumption.write_audio_frame(frame)
            old._on_bot_stopped_speaking()
            return result

    llm = ChannelLLMService(device_id="speaker", channel_server=server,
                            turn_complete_factory=old._channel_turn_complete_callback)
    original_push = llm.push_frame

    async def push(frame, *args):
        if isinstance(frame, LLMFullResponseEndFrame):
            ends.append(frame)
            output_completions.append(frame.metadata["vauxr_output_drained"])
        await original_push(frame, *args)

    monkeypatch.setattr(llm, "push_frame", push)
    original_complete = old._on_turn_complete

    async def complete(*args, **kwargs):
        await original_complete(*args, **kwargs)
        completed.set()

    monkeypatch.setattr(old, "_on_turn_complete", complete)
    output = Output(TransportParams(audio_out_enabled=True, audio_out_sample_rate=24000))
    # Keep the actual Pipecat serialized output and RawAudioTrack; only omit RTP.
    adapter = SimpleNamespace(_client=SimpleNamespace(_audio_output_track=track, _can_send=lambda: True))
    consumption = AudioConsumption(adapter)

    async def receive():
        await release_output.wait()
        while True:
            await track.recv()

    receiver = asyncio.create_task(receive())
    task = PipelineTask(Pipeline([
        llm, realtime_wyoming.WyomingTTSService(),
        output, OutputDrainTap(consumption.drained),
    ]), params=PipelineParams(audio_out_sample_rate=24000))
    old._task = task
    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        await task.queue_frame(LLMContextFrame(LLMContext([{"role": "user", "content": "hello"}])))
        await asyncio.wait_for(ready.wait(), 2)
        abort, = old._channel_media
        authority = server._media_authorities[abort]

        async def dispatch(kind, **payload):
            await server._handle_authenticated_message(a, {
                "type": "channel.response." + kind, "deviceId": "speaker", "runId": "a", **payload})

        if response != "empty":
            await dispatch("delta", text="Partial A speech. ")
            await asyncio.wait_for(synthesis.wait(), 2)
        if response in ("playing", "playing_error"):
            release_tts.set()
            await asyncio.wait_for(playing.wait(), 2)
        await dispatch("error" if "error" in response else "end", message="backend failed")
        await asyncio.wait_for(completed.wait(), 2)
        # Real channel handler finally releases socket authority, never media.
        await socket.close()
        await asyncio.wait_for(socket_task, 2)
        assert a.authority not in auth_connections._connections
        if response != "empty":
            # Expire the exact idle debounce with TTS/output still held. Even
            # repeated/stale stop credits cannot acknowledge this response.
            old._schedule_drain_timer()
            await old._drain_timer
            assert authority in auth_connections._connections
            assert abort in old._channel_media
            assert not abort.is_set()
            assert len(old._pending_ends) == 1
            ws.send_str.assert_not_awaited()
            if response in ("playing", "playing_error"):
                future, = consumption._pending
                assert isinstance(future, asyncio.Future)
                assert not future.done()
                assert abort not in old._drained_media
        assert channel_registry.activate(b.channel.id)
        manager._sessions["speaker"] = replacement
        device_registry.set_state("speaker", "listening")
        states = []
        original_set_state = device_registry.set_state

        def set_state(device_id, state):
            states.append((device_id, state))
            original_set_state(device_id, state)

        monkeypatch.setattr(device_registry, "set_state", set_state)
        ws.send_str.reset_mock()
        await replacement._channel_turn_complete_callback()
        b_abort, = replacement._channel_media
        b_listener = {"on_delta": lambda *args: None, "on_end": lambda *args: None,
                      "on_error": lambda *args: pytest.fail("B notified by A teardown")}
        server.add_response_listener("speaker", b_listener)
        if finish == "drain" or response == "empty":
            release_tts.set()
            release_output.set()
            async with asyncio.timeout(3):
                while abort in old._channel_media:
                    await asyncio.sleep(0)
            assert not old._pending_ends
            ws.send_str.assert_not_awaited()
            assert not consumption._pending
            assert "vauxr_output_drained" not in ends[0].metadata
            assert not abort.is_set()
            old._connection.disconnect.assert_not_awaited()
        else:
            Lifecycle(service.store, ORIGIN).execute("revoke", {
                "operation_id": "d" * 32, "role": "integration", "subject": a.channel.id}, owner)
            assert channel_registry.get_active().id == b.channel.id
            if finish == "retry":
                old._connection.disconnect.side_effect = [RuntimeError("teardown failed"), None]
                with pytest.raises(RuntimeError, match="transport_teardown_unavailable"):
                    await auth_connections.disconnect_stale(service.store)
                assert authority in auth_connections._connections
                assert server._media_turns[abort] == a.channel.id
                assert abort.is_set()
                assert not b_abort.is_set()
                assert not replacement.is_closed
                replacement._connection.disconnect.assert_not_awaited()
            await auth_connections.disconnect_stale(service.store)
            assert abort.is_set()
            assert old._connection.disconnect.await_count == (2 if finish == "retry" else 1)
        await authority.close()
        # Replay the real bound completion captured from A's LLM end frame.
        # It can arrive after drain/teardown and must never complete B's turn.
        assert output_completions
        await output_completions[0]()
        await old._send_audio_end(False)
        await old._notify_ended()
        assert not old._channel_media
        assert not old._drained_media
        assert abort not in server._media_turns
        assert authority not in auth_connections._connections
        ws.send_str.assert_not_awaited()
        assert device_registry.get("speaker").state == "listening"
        assert states == []
        assert not b_abort.is_set()
        assert server._media_turns[b_abort] == b.channel.id
        assert server.get_response_listener("speaker") is b_listener
        assert manager._sessions["speaker"] is replacement
        assert not replacement.is_closed
        replacement._connection.disconnect.assert_not_awaited()
        b.ws.close.assert_not_awaited()
        assert ends  # The actual LLM end, including errors, carries the marker.
    finally:
        release_tts.set()
        release_output.set()
        await old.close()
        await replacement.close()
        await asyncio.wait_for(runner, 3)
        await socket.close()
        await asyncio.wait_for(socket_task, 2)
        auth_connections.release(b.authority)
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        track.stop()
        device_registry.unregister("speaker")
