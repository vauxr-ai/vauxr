"""Clock-driven physical activity and real output-consumption regressions."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('pipecat')
from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
from pipecat.transports.smallwebrtc.transport import RawAudioTrack, SmallWebRTCClient
from vauxr.realtime.device_activity import DeviceActivity


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now
    def advance(self, seconds=1): self.now += seconds


class FirmwareIdle:
    """Model the existing runWarmLoop control semantics, not packet arrival."""
    def __init__(self, clock):
        self.clock = clock
        self.open_mic = True
        self.deadline = clock() + 8
        self.messages = []
        self.tx_muted = False
    async def send(self, message):
        self.messages.append(message)
        # VauxrClient::setState: PROCESSING mutes TX; speech.start keeps it live.
        if message['type'] == 'transcript':
            self.tx_muted = True
        elif message['type'] in ('speech.start', 'audio.start', 'audio.end'):
            self.tx_muted = False
        if message['type'] in ('speech.start', 'transcript', 'audio.start'):
            self.open_mic = False
        elif message['type'] == 'audio.end':
            self.open_mic = message['follow_up']
            self.deadline = self.clock() + 8
        return True
    def expired(self): return self.open_mic and self.clock() >= self.deadline


def setup_activity():
    clock = Clock()
    fw = FirmwareIdle(clock)
    session = SimpleNamespace(_closed=False, _ended_notified=False, _mic_paused=False,
        _handoff_pending=False, _owns_control=lambda: True, _touch_activity=lambda: None,
        _send_control=fw.send, device_id='unit-test-device', close=AsyncMock())
    activity = DeviceActivity(session, clock=clock)
    activity.ready = True
    return clock, fw, session, activity


async def test_speech_work_and_reply_survive_eight_seconds_then_silence_expires():
    clock, fw, session, activity = setup_activity()
    await activity.tick()
    for _ in range(12):
        await activity.transcript('user', 'speech')
        await activity.tick()
        clock.advance()
        assert not fw.expired()
    await activity.delegation('action', True)
    for _ in range(12):
        await activity.tick(); clock.advance()
        assert not fw.expired()
    await activity.delegation('action', False)
    for _ in range(12):
        await activity.provider_audio(b'\x00\x10' * 480)
        await activity.tick(); clock.advance()
        assert not fw.expired()
    clock.advance(2)
    await activity.tick()
    assert fw.messages[-1] == {'type': 'audio.end', 'follow_up': True}
    count = len(fw.messages)
    for _ in range(9):
        clock.advance()
        await activity.provider_audio(bytes(960))
        await activity.tick()
    assert len(fw.messages) == count  # silence must not refresh the idle lease
    assert fw.expired()
    assert not session.close.called


async def test_silent_microphone_uses_real_pinned_vad_without_activity():
    clock, fw, _, activity = setup_activity()
    await activity.tick()
    for _ in range(50):
        await activity.input_audio(InputAudioRawFrame(bytes(960), 24000, 1))
    assert not activity.user_speaking
    assert activity.state == 'listening'
    clock.advance(9)
    await activity.tick()
    assert fw.expired()


async def test_real_output_writer_and_track_consumption_keep_playback_active():
    clock, fw, _, activity = setup_activity()
    track = RawAudioTrack(24000, auto_silence=False)
    client = SimpleNamespace(_audio_output_track=track, _can_send=lambda: True)
    async def write(frame):
        return await SmallWebRTCClient.write_audio_frame(client, frame)
    output = SimpleNamespace(write_audio_frame=write)
    activity.bind_output(output)
    activity.bind_track(track)
    frame = OutputAudioRawFrame(b'\x00\x10' * 480, 24000, 1)
    await activity.provider_audio(frame.audio)
    writer = asyncio.create_task(output.write_audio_frame(frame))
    await asyncio.sleep(.02)
    assert activity.pending_writes == 1
    clock.advance(12)
    await activity.tick()
    assert activity.state == 'output' and not fw.expired()
    await asyncio.wait_for(track.recv(), 1)
    await asyncio.wait_for(track.recv(), 1)  # writer has two 10ms chunks
    await asyncio.wait_for(writer, 1)
    assert activity.pending_writes == 0
    clock.advance(2)
    await activity.tick()
    assert fw.messages[-1]['type'] == 'audio.end'
    track.stop()


async def test_replaced_peer_and_paused_mic_cannot_emit_or_reopen():
    clock, fw, session, activity = setup_activity()
    await activity.provider_audio(b'\x00\x10' * 480)
    session._owns_control = lambda: False
    await activity.tick(); await activity.stop()
    assert not fw.messages and not session.close.called
    _, fw, session, activity = setup_activity()
    session._mic_paused = True
    await activity.transcript('user', 'late')
    await activity.tick()
    assert not fw.messages


async def test_interrupt_invalidates_pending_output_and_cancel_is_terminal():
    clock, fw, session, activity = setup_activity()
    await activity.provider_audio(b'\x00\x10' * 480)
    await activity.tick()
    await activity.interrupt()
    assert fw.messages[-1] == {'type': 'audio.end', 'follow_up': True}
    await activity.stop()
    await asyncio.sleep(0)
    assert fw.messages[-1] == {'type': 'audio.end', 'follow_up': False}
    count = len(fw.messages)
    await activity.provider_audio(b'\x00\x10' * 480)
    await activity.transcript('assistant', 'late')
    await activity.tick()
    assert len(fw.messages) == count
    session.close.assert_awaited_once()


async def test_browser_pipeline_has_no_device_activity(monkeypatch):
    from vauxr.realtime.live import LiveService
    monkeypatch.setenv('OPENAI_API_KEY', 'local-test-only')
    from vauxr.realtime.session import RealtimeSession
    s = RealtimeSession("browser-policy", None)
    llm = LiveService(s, 'test', {'realtime_model':'gpt-live-1','realtime_voice':'cedar'})
    assert llm.device_activity is None
    await llm.cleanup()

async def test_activity_task_survives_registration_gap_and_retires_on_replacement():
    _, fw, session, activity = setup_activity()
    owned = False
    session._owns_control = lambda: owned
    activity.start()
    await asyncio.sleep(.12)
    assert not activity.task.done()
    assert not fw.messages
    owned = True
    await asyncio.sleep(.12)
    assert fw.messages[-1] == {'type':'audio.end', 'follow_up':True}
    owned = False
    count = len(fw.messages)
    await asyncio.sleep(.12)
    assert activity.task.done() and activity.closed
    assert len(fw.messages) == count


async def test_startup_and_backend_stall_are_bounded():
    clock, fw, session, activity = setup_activity()
    activity.ready = False
    await activity.tick()
    assert fw.messages[-1]['type'] == 'speech.start'
    clock.advance(16)
    await activity.tick()
    await asyncio.sleep(0)
    assert activity.closed
    session.close.assert_awaited_once()

    clock, fw, session, activity = setup_activity()
    await activity.transcript('user', 'request')
    await activity.tick()
    clock.advance(301)
    await activity.tick()
    await asyncio.sleep(0)
    assert activity.closed
    session.close.assert_awaited_once()

async def test_processing_lease_preserves_microphone_for_next_utterance():
    clock, fw, _, activity = setup_activity()
    await activity.tick()
    await activity.transcript('user', 'request')
    for _ in range(15):
        await activity.tick()
        assert not fw.tx_muted, 'processing lease must not force silence into Opus'
        assert not fw.expired()
        clock.advance()
    assert not any(m['type'] == 'transcript' for m in fw.messages)
    await activity.delegation('work', True)
    await activity.tick()
    assert not fw.tx_muted
    await activity.delegation('work', False)
    await activity.tick()
    clock.advance(9)
    await activity.tick()
    assert fw.expired(), 'real inactivity still expires after work ends'
