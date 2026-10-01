"""Ordered cold-wake input for the Standard STT/LLM/TTS WebRTC pipeline."""
from pipecat.frames.frames import CancelFrame, EndFrame, InputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from vauxr.realtime.startup import StartupAudio


class StartupInput(FrameProcessor):
    def __init__(self, startup: StartupAudio | None, **kwargs):
        super().__init__(**kwargs)
        self._startup = startup

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        startup = self._startup
        if startup is not None and direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, (CancelFrame, EndFrame)):
                startup.close()
            elif isinstance(frame, InputAudioRawFrame) and startup.append_rtp(frame):
                return
        await self.push_frame(frame, direction)
        if startup is not None and direction == FrameDirection.DOWNSTREAM and isinstance(frame, StartFrame):
            # Queue StartFrame first so downstream VAD/STT initializes before
            # any replayed PCM. StartupAudio serializes WS then early RTP;
            # subsequent RTP passes through only after that queue drains.
            startup.bind(self.push_frame)
            startup.ready()

    async def cleanup(self):
        if self._startup is not None:
            self._startup.close()
        await super().cleanup()
