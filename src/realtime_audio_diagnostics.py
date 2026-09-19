"""Opt-in, content-free Live audio measurements for pinned Pipecat 1.9.0.

At most 300 one-second windows plus start/stop per session, no retained PCM,
transcripts, provider IDs, device IDs, SDP, credentials or arbitrary event text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
from collections import Counter
from collections.abc import Callable

import numpy as np
from av import AudioFrame
from pipecat.frames.frames import CancelFrame, EndFrame, Frame, InterruptionFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transports.smallwebrtc.transport import RawAudioTrack, SmallWebRTCOutputTransport

from realtime_transcript import TranscriptRelay

log = logging.getLogger("vauxr.live_audio")
EVENTS = frozenset({
    "session.started", "session.updated", "session.output_audio.delta", "session.input_transcript.delta",
    "session.output_transcript.delta", "session.delegation.created", "response.event",
    "session.usage.updated",
    "session.closed", "error", "session.start", "session.update", "session.input_audio.append",
    "session.instructions.append", "session.thinking.append", "session.commentary.append", "session.close",
    "response.item.create", "response.create", "response.created", "response.completed",
    "response.incomplete",
    "response.failed", "response.cancelled", "response.output_item.done", "response.cancel",
})
STAGES = ("mic", "provider", "output", "write", "consumed")


class LiveAudioDiagnostics:
    WINDOWS = 300

    def __init__(
        self, relay: TranscriptRelay, *, emit: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self.trace = secrets.token_hex(8)  # Diagnostic-only nonce, unrelated to auth/session identifiers.
        self.relay = relay
        self._emit = emit or (lambda row: log.info("live_audio %s", json.dumps(row, separators=(",", ":"))))
        self.active = True
        self._start = self._window = time.monotonic()
        self._seq = 0
        self._task: asyncio.Task[None] | None = None
        self._counts: Counter[str] = Counter()
        self._pcm: dict[str, list[float]] = {stage: [0, 0, 0, 0, 0] for stage in STAGES}
        self._last_pcm: dict[str, float] = {}
        self._rates: dict[str, int] = {}
        self._last_rx: float | None = None
        self._rx_max_gap = 0.0
        self.handler_started: float | None = None
        self.handler_max_ms = 0.0
        self._write_started: float | None = None
        self._write_max_ms = 0.0
        self._queue_peak = self._track_peak_ms = 0
        self._output: SmallWebRTCOutputTransport | None = None
        self._track: RawAudioTrack | None = None
        self._restore_output: list[Callable[[], None]] = []
        self._restore_track: list[Callable[[], None]] = []

    def count(self, name: str) -> None:
        if self.active:
            self._counts[name] += 1

    def event(self, event_type: str, *, outgoing: bool = False, nested: bool = False) -> None:
        if not self.active:
            return
        # Unknown event payload/type strings must never reach logs.
        prefix = "tx." if outgoing else "nested." if nested else "rx."
        self.count(prefix + (event_type if event_type in EVENTS else "unknown"))
        if not outgoing and not nested:
            now = time.monotonic()
            if self._last_rx is not None:
                self._rx_max_gap = max(self._rx_max_gap, (now - self._last_rx) * 1000)
            self._last_rx = now

    def pcm(self, stage: str, audio: bytes, sample_rate: int = 24000) -> None:
        if not self.active or stage not in STAGES:
            return
        if len(audio) % 2:
            self.count("invalid_pcm_size")
            return
        self._rates[stage] = sample_rate
        samples = np.frombuffer(audio, dtype="<i2")
        values = samples.astype(np.float64) / 32768
        data = self._pcm[stage]
        now = time.monotonic()
        data[0] += 1
        data[1] += len(samples)
        data[2] += float(np.dot(values, values))
        data[3] += int(np.count_nonzero(samples))
        if stage in self._last_pcm:
            data[4] = max(data[4], (now - self._last_pcm[stage]) * 1000)
        self._last_pcm[stage] = now

    def bind_output(self, output: SmallWebRTCOutputTransport) -> None:
        self._output = output
        write, process = output.write_audio_frame, output.process_frame

        async def measured_write(frame: OutputAudioRawFrame) -> bool:
            self.pcm("write", frame.audio, frame.sample_rate)
            self._write_started = time.monotonic()
            try:
                result = await write(frame)
                self.count("write_ok" if result else "write_failed")
                return result
            except asyncio.CancelledError:
                self.count("write_cancelled")
                raise
            except Exception:
                self.count("write_error")
                raise
            finally:
                self._write_max_ms = max(self._write_max_ms, (time.monotonic() - self._write_started) * 1000)
                self._write_started = None

        async def measured_process(frame: Frame, direction: FrameDirection) -> None:
            if isinstance(frame, OutputAudioRawFrame):
                self.pcm("output", frame.audio, frame.sample_rate)
            elif isinstance(frame, InterruptionFrame):
                self.count("pipeline_interruption")
            elif isinstance(frame, CancelFrame):
                self.count("pipeline_cancel")
            elif isinstance(frame, EndFrame):
                self.count("pipeline_end")
            await process(frame, direction)
            self._queue_peak = max(self._queue_peak, sum(
                sender._audio_queue.qsize() for sender in output._media_senders.values()
                if sender._audio_queue))

        output.write_audio_frame = measured_write
        output.process_frame = measured_process
        self._restore_output = [lambda: setattr(output, "write_audio_frame", write),
                                lambda: setattr(output, "process_frame", process)]

    def bind_track(self, track: RawAudioTrack | None) -> None:
        if not self.active or track is None or track is self._track:
            return
        for restore in self._restore_track:
            restore()
        self._track = track
        add, recv = track.add_audio_bytes, track.recv
        added = len(track._chunk_queue)

        def measured_add(audio: bytes) -> asyncio.Future[bool]:
            nonlocal added
            future = add(audio)
            added += len(audio) // track._bytes_per_10ms
            self._track_peak_ms = max(self._track_peak_ms, len(track._chunk_queue) * 10)
            return future  # Preserve the exact consumption future and cancellation semantics.

        async def measured_recv() -> AudioFrame:
            before = added - len(track._chunk_queue)
            frame = await recv()
            # Pipecat 1.9 has one recv consumer and only recv removes chunks.
            # Include additions during recv's timing sleep; checking empty before
            # await alone would misclassify freshly arrived PCM as an underrun.
            if added - len(track._chunk_queue) == before:
                self.count("auto_silence_10ms")
            else:
                self.count("queued_pcm_10ms")
            self.pcm("consumed", frame.to_ndarray().tobytes(), frame.sample_rate)
            return frame

        track.add_audio_bytes = measured_add
        track.recv = measured_recv
        self._restore_track = [lambda: setattr(track, "add_audio_bytes", add),
                               lambda: setattr(track, "recv", recv)]
        self.count("track_bound")

    def sample(self, phase: str = "sample") -> dict[str, object]:
        now = time.monotonic()
        pcm: dict[str, object] = {}
        for stage, (chunks, samples, energy, nonzero, gap) in self._pcm.items():
            rate = self._rates.get(stage, 0)
            pcm[stage] = {"sample_rate": rate, "chunks": int(chunks), "samples": int(samples),
                          "nonzero_samples": int(nonzero),
                          "rms": round(math.sqrt(energy / samples), 7) if samples else 0,
                          "energy": round(energy / rate, 9) if rate > 0 else 0,
                          "max_gap_ms": round(gap, 3),
                          "last_age_ms": round((now - self._last_pcm[stage]) * 1000, 3)
                          if stage in self._last_pcm else -1}
        senders = list(self._output._media_senders.values()) if self._output else []
        row: dict[str, object] = {
            "trace": self.trace, "phase": phase, "seq": self._seq, "wall_ms": int(time.time() * 1000),
            "elapsed_ms": round((now - self._start) * 1000, 3),
            "window_ms": round((now - self._window) * 1000, 3), "pcm": pcm,
            "events": dict(self._counts), "rx_max_gap_ms": round(self._rx_max_gap, 3),
            "rx_last_age_ms": round((now - self._last_rx) * 1000, 3) if self._last_rx is not None else -1,
            "handler_inflight_ms": round((now - self.handler_started) * 1000, 3)
            if self.handler_started is not None else 0, "handler_max_ms": round(self.handler_max_ms, 3),
            "output_queue_frames": sum(s._audio_queue.qsize() for s in senders if s._audio_queue),
            "output_queue_peak_frames": self._queue_peak, "track_queue_peak_ms": self._track_peak_ms,
            "output_buffer_bytes": sum(len(s._audio_buffer) for s in senders),
            "track_queue_ms": len(self._track._chunk_queue) * 10 if self._track else 0,
            "write_inflight_ms": round((now - self._write_started) * 1000, 3)
            if self._write_started is not None else 0, "write_max_ms": round(self._write_max_ms, 3),
            "transcript": self.relay.metrics(reset_window=True),
        }
        self._counts.clear()
        self._pcm = {stage: [0, 0, 0, 0, 0] for stage in STAGES}
        self._window = now
        self._rx_max_gap = self.handler_max_ms = self._write_max_ms = 0.0
        self._queue_peak = self._track_peak_ms = 0
        self._seq += 1
        return row

    def start(self) -> None:
        if self._task is None and self.active:
            self._emit(self.sample("start"))
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            for _ in range(self.WINDOWS):
                await asyncio.sleep(1)
                self._emit(self.sample())
        finally:
            self._finish()

    def _finish(self) -> None:
        if self.active:
            self._emit(self.sample("stop"))
            self.active = False
            for restore in self._restore_output + self._restore_track:
                restore()
            self._restore_output.clear()
            self._restore_track.clear()
            self._track = None
            self._output = None

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._finish()
