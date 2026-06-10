"""Per-device realtime session: a Pipecat WebRTC pipeline wired into vauxr.

A `RealtimeSession` owns one device's WebRTC media pipeline (STT -> channel LLM
-> TTS) and relays turn control (transcript / audio.start / audio.end{follow_up})
back over the device's existing WS connection so the firmware's LED state machine
and follow_up handling work unchanged.

Cold wake: the command spoken right after the wake word is streamed over the
always-on WS while WebRTC connects. On the device-VAD ``voice.end`` marker the
server transcribes that buffered PCM (batch Whisper) and either seeds it into
Pipecat (when WebRTC is connected) or runs the WS turn pipeline (fallback).
Live follow-up turns use WebRTC audio -> VAD -> STT as normal.
"""

from __future__ import annotations

import array
import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

from aiohttp import web

import device_registry as registry
from config import get_config
from device_settings import get_realtime_vad, get_taper
from pipeline import strip_follow_up_tag

log = logging.getLogger("vauxr.realtime")

VOICE_SYSTEM = (
    "You are a helpful voice assistant on a smart speaker. "
    "Responses are spoken aloud — no emojis, markdown, code blocks, or URLs. "
    "Use short, natural sentences. Be concise."
)

# How long the bot must stay silent before a reply counts as fully spoken. Must
# comfortably exceed the gap between consecutive sentence-chunk TTS spans, but
# stay short enough that follow-up/teardown feel responsive.
_BOT_IDLE_DEBOUNCE_S = 1.0

# Coarse last-resort reap for leaked sessions (device lost power with no peer
# close). This is an *inactivity* deadline measured from the last sign of life
# (turn, user speech, or seed) — NOT a fixed wall clock from session start —
# so an actively-used warm session (device re-waking within its taper window) is
# never reaped mid-conversation. Must exceed the device's taper drop timer
# (T_idle1 + T_idle2): a healthy idle device drops its peer (closing the session
# via on_client_disconnected) long before this fires.
_SAFETY_BACKSTOP_S = 600.0
# How often the backstop wakes to check the inactivity deadline.
_SAFETY_BACKSTOP_POLL_S = 30.0


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


def _latest_user_text(context: Any) -> str:
    """Pull the most recent user-role text out of an LLMContext."""
    try:
        messages = context.get_messages()
    except Exception:  # noqa: BLE001
        return ""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, dict)]
            return " ".join(t for t in parts if t).strip()
    return ""


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
        self._backstop_task: asyncio.Task | None = None
        # Monotonic timestamp of the last sign of life; drives the inactivity
        # backstop. Seeded when the pipeline starts.
        self._last_activity = time.monotonic()
        self._closed = False
        # Set once the pipeline is built and the WebRTC client is connected.
        self._pipeline_ready = asyncio.Event()
        # Deferred audio.end queue, in turn order. Each entry is (follow_up,
        # has_audio): a turn that produced bot speech can't end until its bot
        # audio fully drains (counted as a credit), while a silent turn (empty/
        # errored reply) ends as soon as it reaches the head — but never ahead of
        # an earlier still-speaking turn, which would advance turn state while the
        # first reply is still playing.
        self._pending_ends: deque[tuple[bool, bool]] = deque()
        self._bot_stop_credits = 0
        # Bot-speaking bookkeeping. Wyoming TTS speaks one sentence per run_tts,
        # so a single reply produces several BotStarted/BotStoppedSpeaking pairs.
        self._bot_speaking = 0
        self._drain_timer: asyncio.Task | None = None
        # True once we've relayed a terminal audio.end for an abnormal teardown.
        self._ended_notified = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    def is_peer_live(self) -> bool:
        """Whether the WebRTC pipeline is up and the peer has connected."""
        return self._pipeline_ready.is_set() and not self._closed

    # --- lifecycle ---

    async def start(self, connection: Any) -> None:
        """Build and run the pipeline around an established WebRTC connection."""
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.frames.frames import (
            BotStartedSpeakingFrame,
            BotStoppedSpeakingFrame,
            Frame,
            InputAudioRawFrame,
            TranscriptionFrame,
            UserStartedSpeakingFrame,
            UserStoppedSpeakingFrame,
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
        from pipecat.services.tts_service import TextAggregationMode

        from device_settings import get_segmentation
        from realtime_wyoming import WyomingSTTService, WyomingTTSService

        self._connection = connection
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
        )

        stt = WyomingSTTService()
        # Per-device segmentation: sentence mode lets pipecat's TTS aggregator cut
        # on sentence boundaries; otherwise TOKEN mode and the upstream
        # IdleSegmenter (in ChannelLLMService) owns segmentation.
        seg = get_segmentation(self.device_id)
        tts = WyomingTTSService(
            text_aggregation_mode=(
                TextAggregationMode.SENTENCE if seg.sentence else TextAggregationMode.TOKEN
            )
        )
        llm = ChannelLLMService(
            device_id=self.device_id,
            channel_server=self._channel_server,
            on_turn_complete=self._on_turn_complete,
        )

        vad = get_realtime_vad(self.device_id)
        log.info(
            "realtime[%s]: VAD params confidence=%.2f start_secs=%.2f "
            "stop_secs=%.2f min_volume=%.2f",
            self.device_id,
            vad.confidence,
            vad.start_secs,
            vad.stop_secs,
            vad.min_volume,
        )
        context = LLMContext()
        for msg in get_manager().context_messages(self.device_id):
            context.add_message(msg)
        self._context = context
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(
                        confidence=vad.confidence,
                        start_secs=vad.start_secs,
                        stop_secs=vad.stop_secs,
                        min_volume=vad.min_volume,
                    )
                ),
                user_turn_strategies=UserTurnStrategies(
                    start=[VADUserTurnStartStrategy()],
                    stop=[VADStopUserTurnStopStrategy()],
                ),
            ),
        )

        session = self

        class _AudioMeter(FrameProcessor):
            """Log inbound WebRTC audio level ~1×/s so VAD min_volume can be tuned.

            Shows whether the device's mic audio is reaching the server at all and
            how loud it is (peak normalized 0..1, directly comparable to the Silero
            ``min_volume`` gate). Purely diagnostic — passes frames through.
            """

            def __init__(self) -> None:
                super().__init__()
                self._peak = 0
                self._frames = 0
                self._t0 = time.monotonic()

            async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
                await super().process_frame(frame, direction)
                if isinstance(frame, InputAudioRawFrame) and frame.audio:
                    samples = array.array("h")
                    samples.frombytes(frame.audio)
                    if samples:
                        peak = max(abs(max(samples)), abs(min(samples)))
                        self._peak = max(self._peak, peak)
                    self._frames += 1
                    now = time.monotonic()
                    if now - self._t0 >= 1.0:
                        log.info(
                            "realtime[%s]: audio in — %d frames, peak=%d (~%.2f)",
                            session.device_id,
                            self._frames,
                            self._peak,
                            self._peak / 32768.0,
                        )
                        self._peak = 0
                        self._frames = 0
                        self._t0 = now
                await self.push_frame(frame, direction)

        class _ControlTap(FrameProcessor):
            """Relay transcript + bot-speaking events to the device WS."""

            async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
                await super().process_frame(frame, direction)
                if isinstance(frame, UserStartedSpeakingFrame):
                    # Server VAD detected speech onset. Tell the device a turn is
                    # underway so it holds off its active-idle taper — otherwise it
                    # can fall into warm-quiet mid-utterance (before STT/LLM emits
                    # the transcript) and mishandle the late reply.
                    log.info("realtime[%s]: VAD speech START", session.device_id)
                    session._touch_activity()
                    await _send_json(
                        _device_ws(session.device_id), {"type": "speech.start"}
                    )
                elif isinstance(frame, UserStoppedSpeakingFrame):
                    log.info("realtime[%s]: VAD speech STOP", session.device_id)
                elif isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
                    log.info(
                        "realtime[%s]: transcript %r", session.device_id, frame.text.strip()
                    )
                    session._touch_activity()
                    await _send_json(
                        _device_ws(session.device_id),
                        {"type": "transcript", "text": frame.text.strip()},
                    )
                elif isinstance(frame, BotStartedSpeakingFrame):
                    session._on_bot_started_speaking()
                    await _send_json(
                        _device_ws(session.device_id), {"type": "audio.start"}
                    )
                elif isinstance(frame, BotStoppedSpeakingFrame):
                    session._on_bot_stopped_speaking()
                await self.push_frame(frame, direction)

        pipeline = Pipeline(
            [
                transport.input(),
                _AudioMeter(),
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
            self._pipeline_ready.set()

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(_t, _c) -> None:
            if self._closed:
                return
            log.info("realtime[%s]: WebRTC peer dropped", self.device_id)
            await self._notify_ended()
            await self.close()

        self._runner = PipelineRunner(handle_sigint=False)
        self._runner_task = asyncio.create_task(self._runner.run(self._task))
        self._touch_activity()
        self._backstop_task = asyncio.create_task(self._safety_backstop())

    def _touch_activity(self) -> None:
        """Mark a sign of life so the inactivity backstop holds off."""
        self._last_activity = time.monotonic()

    async def _safety_backstop(self) -> None:
        """Reap a session only after prolonged inactivity (leaked peer: device
        lost power with no clean close). Resets on every turn/user-speech, so an
        actively-used warm session is never reaped mid-conversation."""
        try:
            while not self._closed:
                idle = time.monotonic() - self._last_activity
                remaining = _SAFETY_BACKSTOP_S - idle
                if remaining <= 0:
                    break
                await asyncio.sleep(min(_SAFETY_BACKSTOP_POLL_S, remaining))
        except asyncio.CancelledError:
            return
        if not self._closed:
            log.warning(
                "realtime[%s]: safety backstop fired after %.0fs idle — closing session",
                self.device_id,
                _SAFETY_BACKSTOP_S,
            )
            await self._notify_ended()
            await self.close()

    async def _release_failed_turn(self, reason: str) -> None:
        """Tell the device a turn ended with no reply so it leaves PROCESSING.

        Every cold-seed failure path below funnels through here: without an
        audio.end the device sits in PROCESSING until its watchdog fires. We keep
        the session alive (warm) so the user can simply try again.
        """
        log.info("realtime[%s]: cold seed released (%s)", self.device_id, reason)
        if not self._closed:
            await self._send_audio_end(False)

    async def seed_buffered_turn(self, pcm: bytes) -> None:
        """Transcribe cold-wake WS audio and seed one turn into Pipecat."""
        from pipecat.frames.frames import LLMRunFrame

        if self._closed:
            return
        if not pcm:
            await self._release_failed_turn("empty pre-roll")
            return

        try:
            await asyncio.wait_for(self._pipeline_ready.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            log.warning(
                "realtime[%s]: pipeline never ready — dropping buffered seed",
                self.device_id,
            )
            await self._release_failed_turn("pipeline not ready")
            return

        if not self.is_peer_live():
            log.warning(
                "realtime[%s]: peer not live — refusing ghost seed turn",
                self.device_id,
            )
            await self._release_failed_turn("peer not live")
            return

        try:
            from wyoming_stt import transcribe

            text = await transcribe([pcm])
        except Exception as e:  # noqa: BLE001
            log.error("realtime[%s]: buffered transcribe failed: %s", self.device_id, e)
            await self._release_failed_turn("transcribe failed")
            return

        text = (text or "").strip()
        if not text:
            log.info("realtime[%s]: buffered utterance empty after STT", self.device_id)
            await self._release_failed_turn("empty transcript")
            return

        if not self.is_peer_live():
            log.warning(
                "realtime[%s]: peer dropped during STT — refusing ghost seed turn",
                self.device_id,
            )
            await self._release_failed_turn("peer dropped during STT")
            return

        log.info("realtime[%s]: seeding cold->warm turn: %r", self.device_id, text)
        self._touch_activity()
        await _send_json(_device_ws(self.device_id), {"type": "transcript", "text": text})
        self._context.add_message({"role": "user", "content": text})
        if self._task is not None:
            await self._task.queue_frames([LLMRunFrame()])

    async def _on_turn_complete(self, follow_up: bool, reply: str) -> None:
        """Called when an LLM turn ends. Queue its audio.end in turn order."""
        self._touch_activity()
        user_text = _latest_user_text(self._context)
        get_manager().record_turn(self.device_id, user_text, reply)

        has_audio = bool(reply and reply.strip())
        self._pending_ends.append((follow_up, has_audio))
        if has_audio and self._bot_speaking == 0:
            self._schedule_drain_timer()
        await self._drain_ends()

    def _on_bot_started_speaking(self) -> None:
        self._bot_speaking += 1
        self._cancel_drain_timer()

    def _on_bot_stopped_speaking(self) -> None:
        if self._bot_speaking > 0:
            self._bot_speaking -= 1
        if self._bot_speaking == 0:
            self._schedule_drain_timer()

    def _schedule_drain_timer(self) -> None:
        self._cancel_drain_timer()
        self._drain_timer = asyncio.create_task(self._drain_after_idle())

    def _cancel_drain_timer(self) -> None:
        if self._drain_timer is not None:
            self._drain_timer.cancel()
            self._drain_timer = None

    async def _drain_after_idle(self) -> None:
        try:
            await asyncio.sleep(_BOT_IDLE_DEBOUNCE_S)
        except asyncio.CancelledError:
            return
        self._drain_timer = None
        if self._closed or self._bot_speaking != 0:
            return
        if not any(has_audio for _, has_audio in self._pending_ends):
            return
        self._bot_stop_credits += 1
        await self._drain_ends()

    async def _drain_ends(self) -> None:
        """Emit deferred audio.end events in turn order."""
        while self._pending_ends and not self._ended_notified:
            follow_up, has_audio = self._pending_ends[0]
            if has_audio:
                if self._bot_stop_credits <= 0:
                    break
                self._bot_stop_credits -= 1
            self._pending_ends.popleft()
            await self._send_audio_end(follow_up)

    async def _send_audio_end(self, follow_up: bool) -> None:
        await _send_json(
            _device_ws(self.device_id), {"type": "audio.end", "follow_up": follow_up}
        )
        # follow_up:false → Warm-quiet on the device; keep the Pipecat session alive.
        registry.set_state(self.device_id, "listening" if follow_up else "idle")
        if not follow_up:
            log.info(
                "realtime[%s]: follow_up=false — Warm-quiet (session stays alive)",
                self.device_id,
            )

    async def _notify_ended(self) -> None:
        """Relay a terminal audio.end{follow_up:false} for abnormal teardown."""
        if self._ended_notified:
            return
        self._ended_notified = True
        self._pending_ends.clear()
        self._bot_stop_credits = 0
        self._cancel_drain_timer()
        await _send_json(
            _device_ws(self.device_id), {"type": "audio.end", "follow_up": False}
        )
        registry.set_state(self.device_id, "idle")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        log.info("realtime[%s]: closing session", self.device_id)
        self._cancel_drain_timer()
        if self._backstop_task is not None:
            self._backstop_task.cancel()
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
    """Tracks per-device pre-roll buffers, conversation log, and live sessions."""

    def __init__(self) -> None:
        self._channel_server: Any = None
        self._sessions: dict[str, RealtimeSession] = {}
        self._preroll: dict[str, bytearray] = {}
        self._cold_wait: set[str] = set()
        self._conversation_log: dict[str, list[dict[str, str]]] = {}
        self._offer_locks: dict[str, asyncio.Lock] = {}
        self._handler: Any = None
        self._max_preroll_bytes = 16000 * 2 * 10  # 10s @ 16kHz mono
        # Sanity: server backstop must outlive the device taper drop timer.
        taper = get_taper("")
        drop_ms = taper.t_idle1_ms + taper.t_idle2_ms
        if _SAFETY_BACKSTOP_S * 1000 <= drop_ms:
            log.warning(
                "realtime safety backstop (%.0fs) is shorter than device taper drop (%dms)",
                _SAFETY_BACKSTOP_S,
                drop_ms,
            )

    def configure(self, channel_server: Any) -> None:
        self._channel_server = channel_server

    # --- transport-agnostic conversation log ---

    def context_messages(self, device_id: str) -> list[dict[str, str]]:
        """Snapshot of the per-device log for LLMContext reconstruction."""
        return [dict(m) for m in self._conversation_log.get(device_id, [])]

    def record_turn(self, device_id: str, user_text: str, assistant_text: str) -> None:
        """Single choke point for completed turns (WS or realtime)."""
        user_text = (user_text or "").strip()
        assistant_text = strip_follow_up_tag(assistant_text or "").strip()
        if not user_text and not assistant_text:
            return

        messages = self._conversation_log.setdefault(device_id, [])

        if user_text:
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"] = user_text
            else:
                messages.append({"role": "user", "content": user_text})

        if assistant_text:
            if messages and messages[-1]["role"] == "assistant":
                messages[-1]["content"] = assistant_text
            elif messages and messages[-1]["role"] == "user":
                messages.append({"role": "assistant", "content": assistant_text})
            else:
                messages.append({"role": "assistant", "content": assistant_text})

    # --- pre-roll buffering (cold wake: WS audio until voice.end) ---

    def begin_preroll(self, device_id: str) -> None:
        self._preroll[device_id] = bytearray()
        self._cold_wait.add(device_id)
        log.info("realtime[%s]: cold pre-roll capture armed", device_id)

    def add_preroll(self, device_id: str, pcm: bytes) -> None:
        buf = self._preroll.get(device_id)
        if buf is None:
            return
        if len(buf) < self._max_preroll_bytes:
            buf.extend(pcm)

    def take_preroll(self, device_id: str) -> bytes:
        buf = self._preroll.pop(device_id, None)
        return bytes(buf) if buf else b""

    def is_cold_wait(self, device_id: str) -> bool:
        """Whether the device is in cold realtime wait (awaiting voice.end)."""
        return device_id in self._cold_wait

    def has_live_session(self, device_id: str) -> bool:
        """Whether a warm Pipecat session with a live peer exists."""
        session = self._sessions.get(device_id)
        return session is not None and session.is_peer_live()

    async def handle_cold_voice_end(
        self,
        device_id: str,
        *,
        webrtc_connected: bool,
        ws: Any,
        openclaw_client: Any,
        channel_server: Any,
        output_sample_rate: int | None,
    ) -> None:
        """Route the cold-wake utterance: WS pipeline or Pipecat text seed."""
        if device_id not in self._cold_wait:
            return

        self._cold_wait.discard(device_id)
        pcm = self.take_preroll(device_id)

        if webrtc_connected:
            session = self._sessions.get(device_id)
            if session is None or session.is_closed:
                log.warning(
                    "realtime[%s]: webrtc_connected but no session — dropping seed",
                    device_id,
                )
                # Release the device: it's in PROCESSING waiting on this turn.
                await _send_json(ws, {"type": "audio.end", "follow_up": False})
                registry.set_state(device_id, "idle")
                return
            if not pcm:
                log.info("realtime[%s]: empty pre-roll on seed path", device_id)
                await _send_json(ws, {"type": "audio.end", "follow_up": False})
                registry.set_state(device_id, "idle")
                return
            await session.seed_buffered_turn(pcm)
            return

        # WS-only fallback when WebRTC is not connected at end-of-speech.
        if not pcm:
            log.info("realtime[%s]: empty pre-roll on WS branch", device_id)
            await _send_json(ws, {"type": "audio.end", "follow_up": False})
            registry.set_state(device_id, "idle")
            return

        from pipeline import run_voice_turn

        abort = asyncio.Event()
        entry = registry.get(device_id)
        if entry is not None:
            entry.abort_event = abort

        try:
            await run_voice_turn(
                device_id,
                [pcm],
                ws,
                openclaw_client,
                channel_server,
                abort,
                output_sample_rate,
            )
            # Sustained WS fallback: while WebRTC never takes over, the device keeps
            # sending voice.end per turn. Re-arm cold-wait + pre-roll so follow-up WS
            # turns are handled instead of silently ignored. If the turn ended
            # follow_up=false the device tears down and realtime.stop clears this arm.
            if not abort.is_set():
                self.begin_preroll(device_id)
        except Exception as e:  # noqa: BLE001
            log.error("realtime[%s]: WS branch pipeline error: %s", device_id, e)
            await _send_json(
                ws, {"type": "error", "code": "PIPELINE_ERROR", "message": str(e)}
            )
            # run_voice_turn aborted before emitting its own audio.end; release the
            # device so it doesn't hang in PROCESSING.
            await _send_json(ws, {"type": "audio.end", "follow_up": False})
            registry.set_state(device_id, "idle")
        finally:
            e = registry.get(device_id)
            if e is not None:
                e.abort_event = None

    # --- session lifecycle ---

    def forget(self, device_id: str) -> None:
        self._sessions.pop(device_id, None)

    async def abort_wake(self, device_id: str) -> None:
        """Clean up a wake whose /api/offer never produced a usable session."""
        session = self._sessions.get(device_id)
        if session is not None:
            await session.close()
        self._preroll.pop(device_id, None)
        self._cold_wait.discard(device_id)
        ws = _device_ws(device_id)
        if ws is not None:
            await _send_json(ws, {"type": "audio.end", "follow_up": False})
        registry.set_state(device_id, "idle")

    async def stop(self, device_id: str) -> None:
        session = self._sessions.get(device_id)
        if session is not None:
            await session.close()
        self._preroll.pop(device_id, None)
        self._cold_wait.discard(device_id)
        registry.set_state(device_id, "idle")

    def can_accept_offer(self, device_id: str) -> bool:
        """Whether an /api/offer for this device_id is tied to a real wake."""
        return device_id in self._preroll or device_id in self._sessions

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
            existing = self._sessions.get(device_id)
            if existing is not None:
                log.info("realtime[%s]: closing previous session before new offer", device_id)
                await existing.close()
            session = RealtimeSession(device_id, self._channel_server)
            try:
                await session.start(connection)
            except Exception:
                log.exception("realtime[%s]: session start failed", device_id)
                await session.close()
                raise
            self._sessions[device_id] = session

        lock = self._offer_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            return await handler.handle_web_request(req, _on_connection)


_manager: RealtimeManager | None = None


def get_manager() -> RealtimeManager:
    global _manager
    if _manager is None:
        _manager = RealtimeManager()
    return _manager
