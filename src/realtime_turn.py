"""User-turn-stop strategy for batch (segmented) Whisper STT.

Extracted from the realtime PoC. Wyoming/Whisper transcribes per VAD segment and
emits the TranscriptionFrame ~0.5-1s after VAD stop, so we finalize the user turn
when that transcript lands (not on raw VAD stop) — otherwise the turn closes empty
and the LLM runs one turn behind. A short fallback timeout finalizes anyway if no
transcript shows up, so the turn machine never wedges.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy


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
