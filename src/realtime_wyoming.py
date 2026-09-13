"""Pipecat adapters over the shared Wyoming clients.

TTS intentionally buffers a complete segment before handing PCM to Pipecat.
This preserves existing behavior; it is not chunk-streaming latency validation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable

from pipecat.frames.frames import ErrorFrame, Frame, InterruptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601

from speech import Selection, resolve
from wyoming_stt import transcribe
from wyoming_tts import synthesize


class WyomingSTTService(SegmentedSTTService):
    def __init__(self, *, selection: Callable[[], Selection] = resolve, **kwargs) -> None:
        super().__init__(settings=STTSettings(model=None, language=None), **kwargs)
        self._selection = selection

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if len(audio) < int(0.4 * self.sample_rate * 2):
            return
        try:
            selected = self._selection()
            text = await transcribe([audio], sample_rate=self.sample_rate, backend=selected.stt)
            if text.strip():
                yield TranscriptionFrame(text.strip(), self._user_id, time_now_iso8601(), Language.EN)
        except Exception:  # noqa: BLE001
            yield ErrorFrame("Speech STT provider unavailable")


class WyomingTTSService(TTSService):
    def __init__(
        self,
        *,
        selection: Callable[[], Selection] = resolve,
        text_aggregation_mode: TextAggregationMode = TextAggregationMode.TOKEN,
        **kwargs,
    ) -> None:
        super().__init__(
            settings=TTSSettings(model=None, voice=None, language=None),
            text_aggregation_mode=text_aggregation_mode,
            push_start_frame=True,
            push_stop_frames=True,
            **kwargs,
        )
        self._selection = selection
        self._selections: dict[str, Selection] = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, InterruptionFrame):
            self._selections.clear()
        await super().process_frame(frame, direction)

    async def on_turn_context_created(self, context_id: str) -> None:
        # Pipecat may still be draining an earlier context when another reply
        # starts. Bind by context ID rather than a mutable "current reply" slot.
        self._selections[context_id] = self._selection()

    async def on_turn_context_completed(self) -> None:
        context_id = self._turn_context_id
        await super().on_turn_context_completed()
        if context_id is not None:
            self._selections.pop(context_id, None)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        selected = self._selections.get(context_id)
        rate = 22050

        def on_rate(value: int) -> None:
            nonlocal rate
            rate = value

        try:
            if selected is None:
                raise RuntimeError("Speech reply has no selection snapshot")
            parts = [part async for part in synthesize(text, selection=selected, on_sample_rate=on_rate)]

            async def pcm_stream() -> AsyncIterator[bytes]:
                for part in parts:
                    yield part

            async for frame in self._stream_audio_frames_from_iterator(
                pcm_stream(),
                in_sample_rate=rate,
                context_id=context_id,
            ):
                yield frame
        except Exception:  # noqa: BLE001
            yield ErrorFrame("Speech TTS provider unavailable")
