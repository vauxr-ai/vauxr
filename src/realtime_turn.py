"""User-turn-stop strategy for batch (segmented) Whisper STT.

Extracted from the realtime PoC. Wyoming/Whisper transcribes per VAD segment and
emits the TranscriptionFrame ~0.5-1s after VAD stop, so we finalize the user turn
when that transcript lands (not on raw VAD stop) — otherwise the turn closes empty
and the LLM runs one turn behind. A short fallback timeout finalizes anyway if no
transcript shows up, so the turn machine never wedges.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.vad_user_turn_start_strategy import (
    VADUserTurnStartStrategy,
)
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy


class SuppressibleVADUserTurnStartStrategy(VADUserTurnStartStrategy):
    """VAD turn-start strategy that ignores new user turns while a reply pends.

    During the PROCESSING window — the user has finished, the LLM reply is being
    generated, and the bot has not started speaking yet — the device's residual
    AEC echo (or room noise) can trip the server VAD and start a *new* user turn.
    Pipecat treats that as a barge-in and broadcasts an interruption that cancels
    the in-flight LLM turn, dropping a real reply (the device then hangs in
    PROCESSING until its watchdog tapers it to warm-quiet).

    Barge-in only makes sense once the bot is actually speaking, so we suppress
    new user-turn starts during that pre-speech window. ``is_suppressed`` is
    polled per VAD-start event; the owning session returns True from the moment a
    real transcript is in flight until the bot starts speaking (or the turn ends
    with no reply), at which point genuine barge-in over the reply works again.
    Suppressing here (rather than gating audio) keeps VAD/STT and the current
    turn's stop detection fully intact — only the *next* turn's start is held.
    """

    def __init__(self, *, is_suppressed: Callable[[], bool], **kwargs):
        super().__init__(**kwargs)
        self._is_suppressed = is_suppressed

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, VADUserStartedSpeakingFrame) and self._is_suppressed():
            # Swallow the start: do not trigger a user turn, so the aggregator
            # never broadcasts an interruption against the pending reply.
            return ProcessFrameResult.CONTINUE
        return await super().process_frame(frame)


class VADStopUserTurnStopStrategy(BaseUserTurnStopStrategy):
    """End the user turn when the batch transcript lands after VAD stop."""

    def __init__(self, *, fallback_timeout: float = 2.0, **kwargs):
        super().__init__(**kwargs)
        self._fallback_timeout = fallback_timeout
        self._vad_stopped = False
        self._fallback_task: asyncio.Task | None = None

    async def reset(self):
        await super().reset()
        self._vad_stopped = False
        await self._cancel_fallback()

    async def cleanup(self):
        await super().cleanup()
        await self._cancel_fallback()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._vad_stopped = False
            await self._cancel_fallback()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._vad_stopped = True
            await self._start_fallback()
        elif isinstance(frame, TranscriptionFrame):
            if self._vad_stopped and frame.text and frame.text.strip():
                await self._finalize()
        return ProcessFrameResult.CONTINUE

    async def _start_fallback(self):
        await self._cancel_fallback()
        self._fallback_task = self.task_manager.create_task(
            self._fallback_handler(), f"{self}::_fallback_handler"
        )
        await asyncio.sleep(0)

    async def _fallback_handler(self):
        try:
            await asyncio.sleep(self._fallback_timeout)
        except asyncio.CancelledError:
            return
        finally:
            self._fallback_task = None
        await self._finalize()

    async def _finalize(self):
        if not self._vad_stopped:
            return
        self._vad_stopped = False
        await self._cancel_fallback()
        await self.trigger_user_turn_stopped()

    async def _cancel_fallback(self):
        if self._fallback_task:
            await self.task_manager.cancel_task(self._fallback_task)
            self._fallback_task = None
