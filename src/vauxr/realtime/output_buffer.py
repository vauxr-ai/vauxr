"""Bounded response prefill for bursty speech, before transport pacing.

Keep the stock small writes and consumption acknowledgements unchanged. This
adds startup headroom; it is not a cure for sustained producer starvation.
"""
from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    CancelFrame, EndFrame, InterruptionFrame, LLMFullResponseStartFrame,
    LLMFullResponseEndFrame, OutputAudioRawFrame, TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class OutputJitterBuffer(FrameProcessor):
    def __init__(self, *, lead_ms: float = 120, **kwargs):
        super().__init__(**kwargs)
        if not 0 < lead_ms <= 500:
            raise ValueError("lead_ms must be in (0, 500]")
        self._lead_ms = lead_ms
        self._held = []
        self._held_ms = 0.0
        self._priming = True
        self._epoch = 0
        self._timer = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _stop_timer(self):
        task, self._timer = self._timer, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _flush(self, epoch):
        frames, self._held = self._held, []
        self._held_ms = 0.0
        self._priming = False
        for frame in frames:
            if self._closed or epoch != self._epoch:
                break
            await self.push_frame(frame)

    async def _timeout(self, epoch):
        try:
            await asyncio.sleep(self._lead_ms / 1000)
            async with self._lock:
                if not self._closed and epoch == self._epoch:
                    await self._flush(epoch)
        except asyncio.CancelledError:
            pass
        finally:
            if self._timer is asyncio.current_task():
                self._timer = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (InterruptionFrame, CancelFrame)):
            self._epoch += 1
            await self._stop_timer()
            async with self._lock:
                self._held.clear()
                self._held_ms = 0.0
                self._priming = True
                if isinstance(frame, CancelFrame):
                    self._closed = True
                await self.push_frame(frame, direction)
            return
        if isinstance(frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame,
                              TTSStoppedFrame, EndFrame)):
            async with self._lock:
                # Let an in-progress timed flush finish before a normal marker.
                # Cancelling it mid-push could lose the detached tail.
                await self._stop_timer()
                await self._flush(self._epoch)
                # TTSStopped may mark a sentence, not a new response. Do not
                # add a fresh prefill pause between sentences of one reply.
                if isinstance(frame, LLMFullResponseStartFrame):
                    self._epoch += 1
                    self._priming = True
                if isinstance(frame, EndFrame):
                    self._closed = True
                await self.push_frame(frame, direction)
            return
        if not isinstance(frame, OutputAudioRawFrame) or not frame.audio:
            await self.push_frame(frame, direction)
            return
        async with self._lock:
            if self._closed:
                return
            if not self._priming:
                await self.push_frame(frame, direction)
                return
            self._held.append(frame)
            self._held_ms += len(frame.audio) * 1000 / (2 * frame.num_channels * frame.sample_rate)
            if self._held_ms >= self._lead_ms:
                # A pending timer also needs the lock. Cancel without awaiting
                # under the lock; its epoch/closed checks prevent late output.
                task, self._timer = self._timer, None
                if task:
                    task.cancel()
                await self._flush(self._epoch)
            elif self._timer is None:
                self._timer = asyncio.create_task(self._timeout(self._epoch))

    async def cleanup(self):
        self._closed = True
        self._epoch += 1
        await self._stop_timer()
        async with self._lock:
            self._held.clear()
            self._held_ms = 0.0
        await super().cleanup()
