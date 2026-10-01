"""One cold wake's bounded, ordered WS PCM -> RTP input cutover."""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable


class StartupAudio:
    MAX_AUDIO_SECONDS = 15
    TIMEOUT_SECONDS = 20.0

    def __init__(self, startup_id: int, fail: Callable[[str], Awaitable[None]],
                 *, input_rate: int = 24000) -> None:
        if input_rate not in (16000, 24000):
            raise ValueError("Unsupported startup input rate")
        self.input_rate = input_rate
        self.id = startup_id
        self.closed = False
        self.complete = False
        self.provider_ready = False
        self.drained = False
        self.next_seq = 0
        self._ws = bytearray()
        self._rtp: deque[Any] = deque()
        self._bytes_24k = 0
        self._consume: Callable[[Any], Awaitable[None]] | None = None
        self._fail = fail
        self._drain_task: asyncio.Task | None = None
        self._timer = asyncio.get_running_loop().call_later(
            self.TIMEOUT_SECONDS, self.fail, "REALTIME_STARTUP_TIMEOUT")

    def close(self) -> None:
        self.closed = True
        self._timer.cancel()
        self._ws.clear()
        self._rtp.clear()
        self._bytes_24k = 0
        if self._drain_task and self._drain_task is not asyncio.current_task():
            self._drain_task.cancel()

    def fail(self, code: str) -> None:
        if self.closed:
            return
        self.close()
        asyncio.create_task(self._fail(code))

    def _reserve(self, size: int, rate: int) -> bool:
        self._bytes_24k += size * 24000 // rate
        if self._bytes_24k > self.MAX_AUDIO_SECONDS * 24000 * 2:
            self.fail("REALTIME_STARTUP_OVERFLOW")
            return False
        return True

    def append_ws(self, sequence: int, pcm: bytes) -> None:
        if self.closed or self.complete:
            return
        if sequence != self.next_seq or not pcm or len(pcm) % 2:
            self.fail("REALTIME_STARTUP_SEQUENCE")
            return
        if self._reserve(len(pcm), 16000):
            self._ws.extend(pcm)
            self.next_seq = (self.next_seq + 1) & 0xffff

    def append_rtp(self, frame: Any) -> bool:
        """True means startup owns the frame, including discarded closed wakes."""
        if self.closed:
            return True
        if self.drained:
            return False
        if self._reserve(len(frame.audio), frame.sample_rate):
            self._rtp.append(frame)
        return True

    def finish_ws(self, startup_id: int, next_seq: int) -> bool:
        if self.closed or startup_id != self.id:
            return False
        if next_seq != self.next_seq:
            self.fail("REALTIME_STARTUP_SEQUENCE")
            return False
        self.complete = True
        self._maybe_drain()
        return True

    def bind(self, consume: Callable[[Any], Awaitable[None]]) -> None:
        self._consume = consume
        self._maybe_drain()

    def ready(self) -> None:
        self.provider_ready = True
        self._maybe_drain()

    def _maybe_drain(self) -> None:
        if (not self.closed and not self.drained and self.complete and self.provider_ready
                and self._consume is not None and self._drain_task is None):
            self._drain_task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        from pipecat.audio.resamplers.soxr_resampler import SOXRAudioResampler
        from pipecat.frames.frames import InputAudioRawFrame

        try:
            # Flush the entire finite WS segment through a batch resampler. A
            # streaming resampler would retain its tail when input switches to
            # RTP's already-resampled pipeline-rate frames.
            pcm = bytes(self._ws)
            if pcm and self.input_rate != 16000:
                pcm = await SOXRAudioResampler().resample(pcm, 16000, self.input_rate)
            self._ws.clear()
            chunk_bytes = self.input_rate * 2 * 20 // 1000
            for offset in range(0, len(pcm), chunk_bytes):
                if self.closed:
                    return
                await self._consume(InputAudioRawFrame(
                    audio=pcm[offset:offset + chunk_bytes], sample_rate=self.input_rate, num_channels=1))
            self._bytes_24k -= len(pcm) * 24000 // self.input_rate
            while self._rtp and not self.closed:
                frame = self._rtp.popleft()
                await self._consume(frame)
                self._bytes_24k -= len(frame.audio) * 24000 // frame.sample_rate
            if not self.closed:
                # No await between observing an empty FIFO and releasing live
                # input. A later process_frame cannot overtake the final drain.
                self.drained = True
                self._timer.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail("REALTIME_STARTUP_FAILED")
