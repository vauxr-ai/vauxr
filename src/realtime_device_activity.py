"""Physical GPT-Live activity translated to existing firmware lifecycle controls.

Silence/packet arrival is not activity. Browser sessions do not use this bridge.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable
import numpy as np

log = logging.getLogger('vauxr.device_activity')


def audible(pcm: bytes) -> bool:
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
    return bool(samples.size and np.mean(samples * samples) > 100 ** 2)


class DeviceActivity:
    QUIET_GAP = 1.0
    WORK_LIMIT = 300.0
    START_LIMIT = 15.0

    def __init__(self, session: Any, *, clock: Callable[[], float] = time.monotonic):
        self.session, self.clock = session, clock
        self.state = 'startup'
        self.changed = clock()
        self.last_sent = self.last_output = self.last_input = float('-inf')
        self.user_speaking = self.processing = self.closed = self.ready = False
        self.delegations: set[str] = set()
        self.pending_writes = self.epoch = self.revision = 0
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self._vad = self._resampler = None

    def owns(self):
        return (not self.closed and not self.session._closed and
                not self.session._ended_notified and self.session._owns_control())

    def admitted(self):
        return self.owns() and not self.session._handoff_pending and not self.session._mic_paused

    def start(self):
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        # start_live precedes manager registration; do not exit during that gap.
        owned = False
        while not self.closed and not self.session._closed:
            if self.owns():
                owned = True
                await self.tick()
            elif owned:
                self.close()
                return
            await asyncio.sleep(.1)

    def _state(self, state):
        if self.state != state:
            self.state = state
            self.revision += 1
            self.changed = self.clock()
            self.last_sent = float('-inf')

    async def input_audio(self, frame):
        if not self.admitted():
            return
        if self._vad is None:
            from pipecat.audio.vad.silero import SileroVADAnalyzer
            from pipecat.audio.vad.vad_analyzer import VADParams
            from pipecat.audio.resamplers.soxr_resampler import SOXRAudioResampler
            self._vad = SileroVADAnalyzer(params=VADParams(confidence=.8, start_secs=.2, stop_secs=.5))
            self._vad.set_sample_rate(16000)
            self._resampler = SOXRAudioResampler()
        from pipecat.audio.vad.vad_analyzer import VADState
        epoch = self.epoch
        pcm = await self._resampler.resample(frame.audio, frame.sample_rate, 16000)
        state = await self._vad.analyze_audio(pcm)
        if epoch != self.epoch or not self.admitted():
            return
        if state == VADState.SPEAKING:
            self.last_input = self.clock()
            if not self.user_speaking:
                self.user_speaking = True
                self.processing = False
                self._state('speech')
        elif state == VADState.QUIET and self.user_speaking:
            self.user_speaking = False
            if self.state != 'output':
                self.processing = True
                self._state('processing')

    async def transcript(self, role, delta):
        if not delta.strip() or not self.admitted():
            return
        if role == 'user':
            self.last_input = self.clock()
            self.processing = True
            self._state('speech' if self.user_speaking else 'processing')
        else:
            self.last_output = self.clock()
            self.processing = False
            self._state('output')

    async def provider_audio(self, pcm):
        if self.admitted() and audible(pcm):
            self.processing = False
            self.last_output = self.clock()
            self._state('output')

    async def delegation(self, key, active):
        if not self.admitted():
            return
        if active:
            self.delegations.add(key)
            if self.state != 'output':
                self._state('processing')
        else:
            self.delegations.discard(key)
            if not self.delegations:
                self.processing = False

    async def tick(self):
        async with self.lock:
            if not self.admitted():
                return
            now = self.clock()
            if self.state == 'startup':
                if self.ready:
                    self._state('listening')
                elif now - self.changed >= self.START_LIMIT:
                    await self.stop()
                    return
            if self.state == 'speech' and now - self.last_input > self.QUIET_GAP:
                self.user_speaking = False
                self.processing = True
                self._state('processing')
            if self.state == 'output' and not self.pending_writes and now - self.last_output >= self.QUIET_GAP:
                self._state('speech' if self.user_speaking else
                            'processing' if self.delegations or self.processing else 'listening')
            if self.state == 'processing' and not self.processing and not self.delegations:
                self._state('listening')
            if self.state != 'listening' and now - self.changed >= self.WORK_LIMIT:
                await self.stop()
                return
            if self.state == 'listening':
                if self.last_sent != float('-inf'):
                    return
                message = {'type': 'audio.end', 'follow_up': True}
            elif now - self.last_sent < 1:
                return
            elif self.state == 'output':
                message = {'type': 'audio.start', 'sample_rate': 24000}
            else:
                # Legacy transcript controls put firmware in PROCESSING, which
                # intentionally substitutes silence for microphone PCM. Live
                # must remain full-duplex during provider/backend work. The
                # speech/activity control holds its idle timer without muting TX.
                message = {'type': 'speech.start'}
            revision = self.revision
            first_notice = self.last_sent == float('-inf')
            try:
                sent = await asyncio.wait_for(self.session._send_control(message), 1)
            except (TimeoutError, ConnectionError):
                sent = False
            if not sent:
                await self.stop()
                return
            if revision == self.revision and self.admitted():
                self.last_sent = now
                self.session._touch_activity()
                if first_notice:
                    log.info('physical_live activity=%s control=%s', self.state, message['type'])

    def bind_output(self, output):
        write = output.write_audio_frame
        async def tracked(frame):
            epoch = self.epoch
            meaningful = self.admitted() and audible(frame.audio)
            if meaningful:
                self.pending_writes += 1
            try:
                return await write(frame)
            finally:
                if meaningful and epoch == self.epoch:
                    self.pending_writes -= 1
        output.write_audio_frame = tracked

    def bind_track(self, track):
        recv = track.recv
        async def consumed():
            epoch = self.epoch
            frame = await recv()
            if epoch == self.epoch and self.admitted() and audible(frame.to_ndarray().tobytes()):
                self.last_output = self.clock()
                self.processing = False
                self._state('output')
            return frame
        track.recv = consumed

    async def interrupt(self):
        if not self.admitted():
            return
        self.epoch += 1
        self.pending_writes = 0
        self.processing = self.user_speaking = False
        self.last_output = float('-inf')
        self._state('listening')
        await self.tick()

    async def stop(self):
        if self.closed:
            return
        owned = self.owns()
        if owned:
            try:
                await asyncio.wait_for(self.session._send_control({'type':'audio.end', 'follow_up':False}), 1)
            except (TimeoutError, ConnectionError):
                pass
        self.close()
        if owned:
            asyncio.create_task(self.session.close())

    def close(self):
        self.closed = True
        self.epoch += 1
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
