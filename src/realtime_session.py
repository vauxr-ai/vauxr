"""Per-device realtime session: a Pipecat WebRTC pipeline wired into vauxr.

A `RealtimeSession` owns one device's WebRTC media pipeline (STT -> channel LLM
-> TTS) and relays turn control (transcript / audio.start / audio.end{follow_up})
back over the device's existing WS connection so the firmware's LED state machine
and follow_up handling work unchanged.

Pre-roll: the command spoken right after the wake word is streamed over the
always-on WS while WebRTC connects. We buffer that PCM, batch-transcribe it with
the same Whisper the WS pipeline uses, and seed it into the pipeline as a text
turn (context message + LLMRunFrame). Live follow-up turns use WebRTC
audio -> VAD -> STT as normal.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

import device_registry as registry
from config import get_config

log = logging.getLogger("vauxr.realtime")

VOICE_SYSTEM = (
    "You are a helpful voice assistant on a smart speaker. "
    "Responses are spoken aloud — no emojis, markdown, code blocks, or URLs. "
    "Use short, natural sentences. Be concise."
)


async def _send_json(ws: Any, obj: dict[str, Any]) -> None:
    if ws is None or getattr(ws, "closed", True):
        return
    try:
        await ws.send_str(json.dumps(obj, separators=(",", ":")))
    except (ConnectionResetError, RuntimeError):
        pass


def _device_ws(device_id: str) -> web.WebSocketResponse | None:
    entry = registry.get(device_id)
    return entry.ws if entry is not None else None


class RealtimeSession:
    """One device's live WebRTC pipeline + WS control relay."""

    def __init__(self, device_id: str, channel_server: Any) -> None:
        self.device_id = device_id
        self._channel_server = channel_server
        self._task: Any = None
        self._runner: Any = None
        self._context: Any = None
        self._connection: Any = None
        self._runner_task: asyncio.Task | None = None
        self._closed = False

    # --- lifecycle ---

    async def start(self, connection: Any) -> None:
        """Build and run the pipeline around an established WebRTC connection."""
        # Imports are local so the realtime (pipecat) dependency only loads when
        # a realtime session actually starts.
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.frames.frames import (
            BotStartedSpeakingFrame,
            Frame,
            TranscriptionFrame,
        )
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineParams, PipelineTask
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair,
            LLMUserAggregatorParams,
        )
        from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
        from pipecat.transports.base_transport import TransportParams
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat.turns.user_start import VADUserTurnStartStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies

        from realtime_llm import ChannelLLMService
        from realtime_turn import VADStopUserTurnStopStrategy
        from realtime_wyoming import WyomingSTTService, WyomingTTSService

        self._connection = connection
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )

        stt = WyomingSTTService()
        tts = WyomingTTSService()
        llm = ChannelLLMService(
            device_id=self.device_id,
            channel_server=self._channel_server,
            on_turn_complete=self._on_turn_complete,
        )

        context = LLMContext()
        self._context = context
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=0.85,
                        start_secs=0.4,
                        stop_secs=0.6,
                        min_volume=0.8,
                    )
                ),
                user_turn_strategies=UserTurnStrategies(
                    start=[VADUserTurnStartStrategy()],
                    stop=[VADStopUserTurnStopStrategy()],
                ),
            ),
        )

        session = self

        class _ControlTap(FrameProcessor):
            """Relay transcript + bot-speaking events to the device WS."""

            async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
                await super().process_frame(frame, direction)
                if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
                    await _send_json(
                        _device_ws(session.device_id),
                        {"type": "transcript", "text": frame.text.strip()},
                    )
                elif isinstance(frame, BotStartedSpeakingFrame):
                    await _send_json(
                        _device_ws(session.device_id), {"type": "audio.start"}
                    )
                await self.push_frame(frame, direction)

        pipeline = Pipeline(
            [
                transport.input(),
                stt,
                _ControlTap(),
                user_aggregator,
                llm,
                tts,
                transport.output(),
                assistant_aggregator,
            ]
        )

        self._task = PipelineTask(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        )

        @transport.event_handler("on_client_connected")
        async def _on_connected(_t, _c) -> None:
            log.info("realtime[%s]: WebRTC client connected", self.device_id)
            await self._flush_preroll()

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(_t, _c) -> None:
            log.info("realtime[%s]: WebRTC client disconnected", self.device_id)
            await self.close()

        self._runner = PipelineRunner(handle_sigint=False)
        self._runner_task = asyncio.create_task(self._runner.run(self._task))

    async def _flush_preroll(self) -> None:
        """Transcribe buffered WS pre-roll and seed it as the first turn."""
        from pipecat.frames.frames import LLMRunFrame

        mgr = get_manager()
        pcm = mgr.take_preroll(self.device_id)
        if not pcm:
            return
        try:
            from wyoming_stt import transcribe

            text = await transcribe([pcm])
        except Exception as e:  # noqa: BLE001
            log.error("realtime[%s]: pre-roll transcribe failed: %s", self.device_id, e)
            return
        text = (text or "").strip()
        if not text:
            log.info("realtime[%s]: pre-roll empty after STT", self.device_id)
            return
        log.info("realtime[%s]: seeding pre-roll turn: %r", self.device_id, text)
        await _send_json(_device_ws(self.device_id), {"type": "transcript", "text": text})
        self._context.add_message({"role": "user", "content": text})
        if self._task is not None:
            await self._task.queue_frames([LLMRunFrame()])

    async def _on_turn_complete(self, follow_up: bool, _reply: str) -> None:
        """Each turn ends here: relay audio.end{follow_up}; exit if not following up."""
        await _send_json(
            _device_ws(self.device_id), {"type": "audio.end", "follow_up": follow_up}
        )
        registry.set_state(self.device_id, "listening" if follow_up else "idle")
        if not follow_up:
            log.info("realtime[%s]: follow_up=false — ending realtime session", self.device_id)
            # Give the final TTS a moment to drain over WebRTC before teardown;
            # the firmware also tears down its side on audio.end{follow_up:false}.
            asyncio.create_task(self._deferred_close())

    async def _deferred_close(self) -> None:
        await asyncio.sleep(0.5)
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        log.info("realtime[%s]: closing session", self.device_id)
        try:
            if self._task is not None:
                await self._task.cancel()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._connection is not None:
                await self._connection.disconnect()
        except Exception:  # noqa: BLE001
            pass
        get_manager().forget(self.device_id)


class RealtimeManager:
    """Tracks per-device pre-roll buffers and live sessions."""

    def __init__(self) -> None:
        self._channel_server: Any = None
        self._sessions: dict[str, RealtimeSession] = {}
        self._preroll: dict[str, bytearray] = {}
        self._handler: Any = None
        self._max_preroll_bytes = 16000 * 2 * 10  # 10s @ 16kHz mono

    def configure(self, channel_server: Any) -> None:
        self._channel_server = channel_server

    # --- pre-roll buffering (driven by WS realtime.start + 0x01 frames) ---

    def begin_preroll(self, device_id: str) -> None:
        self._preroll[device_id] = bytearray()
        log.info("realtime[%s]: pre-roll capture armed", device_id)

    def add_preroll(self, device_id: str, pcm: bytes) -> None:
        buf = self._preroll.get(device_id)
        if buf is None:
            return
        if len(buf) < self._max_preroll_bytes:
            buf.extend(pcm)

    def take_preroll(self, device_id: str) -> bytes:
        buf = self._preroll.pop(device_id, None)
        return bytes(buf) if buf else b""

    # --- session lifecycle ---

    def forget(self, device_id: str) -> None:
        self._sessions.pop(device_id, None)
        self._preroll.pop(device_id, None)

    async def stop(self, device_id: str) -> None:
        session = self._sessions.get(device_id)
        if session is not None:
            await session.close()

    def _request_handler(self) -> Any:
        if self._handler is None:
            from pipecat.transports.smallwebrtc.connection import IceServer
            from pipecat.transports.smallwebrtc.request_handler import (
                ConnectionMode,
                SmallWebRTCRequestHandler,
            )

            cfg = get_config().realtime
            ice = [IceServer(urls=cfg.stun_url)] if cfg.stun_url else None
            self._handler = SmallWebRTCRequestHandler(
                ice_servers=ice,
                esp32_mode=cfg.esp32_mode,
                host=cfg.host or None,
                connection_mode=ConnectionMode.MULTIPLE,
            )
        return self._handler

    async def handle_offer(self, device_id: str, body: dict[str, Any]) -> dict[str, str] | None:
        """Build/refresh a session around an incoming SDP offer."""
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

        handler = self._request_handler()
        req = SmallWebRTCRequest.from_dict(
            {k: body[k] for k in ("sdp", "type", "pc_id", "restart_pc") if k in body}
        )

        async def _on_connection(connection: Any) -> None:
            session = RealtimeSession(device_id, self._channel_server)
            self._sessions[device_id] = session
            await session.start(connection)

        return await handler.handle_web_request(req, _on_connection)


_manager: RealtimeManager | None = None


def get_manager() -> RealtimeManager:
    global _manager
    if _manager is None:
        _manager = RealtimeManager()
    return _manager
