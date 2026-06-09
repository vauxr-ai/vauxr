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
from collections import deque
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
        self._preroll_task: asyncio.Task | None = None
        self._closed = False
        # Set once the pipeline is built and the WebRTC client is connected, so
        # pre-roll flush (driven by realtime.media_ready) can wait for it.
        self._pipeline_ready = asyncio.Event()
        # Deferred audio.end queue, in turn order. Each entry is (follow_up,
        # has_audio): a turn that produced bot speech can't end until its
        # BotStoppedSpeaking arrives (counted as a credit), while a silent turn
        # (empty/errored reply) ends as soon as it reaches the head — but never
        # ahead of an earlier still-speaking turn, which would tear the session
        # down while the first reply is still playing.
        self._pending_ends: deque[tuple[bool, bool]] = deque()
        self._bot_stop_credits = 0
        # True once the device has been told the session ended (audio.end with
        # follow_up=false). Guards against double-sending and ensures an abnormal
        # teardown (peer drop, error) still relays a terminal audio.end so the
        # device leaves listening/speaking instead of hanging.
        self._ended_notified = False

    # --- lifecycle ---

    async def start(self, connection: Any) -> None:
        """Build and run the pipeline around an established WebRTC connection."""
        # Imports are local so the realtime (pipecat) dependency only loads when
        # a realtime session actually starts.
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.frames.frames import (
            BotStartedSpeakingFrame,
            BotStoppedSpeakingFrame,
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
                elif isinstance(frame, BotStoppedSpeakingFrame):
                    # The bot finished talking — now (and only now) is the turn's
                    # audio actually done, so relay the deferred audio.end.
                    await session._on_bot_stopped_speaking()
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
            # Don't flush pre-roll here — the device is still sending wake-word
            # audio over WS until it signals realtime.media_ready. Just mark the
            # pipeline ready; the flush is triggered by media_ready.
            self._pipeline_ready.set()

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(_t, _c) -> None:
            if self._closed:
                # We initiated this teardown (close() sets _closed before it
                # disconnects the peer) — e.g. a graceful end or an /api/offer
                # supersede/renegotiation. The peer didn't drop, so don't relay
                # a spurious audio.end that would kick the device out mid-turn.
                return
            log.info("realtime[%s]: WebRTC peer dropped", self.device_id)
            # Peer dropped (ICE/network failure). The control WS is still up, so
            # relay a terminal audio.end — otherwise the device can sit in
            # listening/speaking after we've torn the pipeline down.
            await self._notify_ended()
            await self.close()

        self._runner = PipelineRunner(handle_sigint=False)
        self._runner_task = asyncio.create_task(self._runner.run(self._task))
        self._preroll_task = asyncio.create_task(self._flush_preroll_when_ready())

    async def _flush_preroll_when_ready(self) -> None:
        """Flush pre-roll once the pipeline is up AND the device signals it's done.

        Waiting on both avoids two races: flushing before the pipeline can accept
        the seeded turn, and flushing before the device has sent all its WS
        pre-roll (which would drop the tail of the first utterance).
        """
        media_ready = get_manager().media_ready_event(self.device_id)
        try:
            await asyncio.wait_for(self._pipeline_ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            # Pipeline never came up — we can't seed a turn. Drop the buffer so it
            # doesn't linger until the next wake.
            log.warning("realtime[%s]: pipeline never ready — dropping pre-roll", self.device_id)
            get_manager().take_preroll(self.device_id)
            return
        try:
            await asyncio.wait_for(media_ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            # Device never confirmed media_ready, but the pipeline is up. Flush
            # what we buffered anyway rather than silently discarding the user's
            # first command after wake.
            log.warning(
                "realtime[%s]: media_ready timed out — flushing pre-roll anyway", self.device_id
            )
        if self._closed:
            return
        await self._flush_preroll()

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

    async def _on_turn_complete(self, follow_up: bool, reply: str) -> None:
        """Called when an LLM turn ends. Queue its audio.end in turn order.

        A turn with spoken text waits for its BotStoppedSpeaking (TTS drained)
        before its audio.end fires, so the firmware doesn't advance turn state —
        or, for follow_up=false, tear down WebRTC — while the reply is playing.
        A silent turn has no TTS so it can end immediately, but only once it
        reaches the head of the queue (see _drain_ends).
        """
        has_audio = bool(reply and reply.strip())
        self._pending_ends.append((follow_up, has_audio))
        await self._drain_ends()

    async def _on_bot_stopped_speaking(self) -> None:
        """Bot finished a turn's audio — credit it and drain ready ends."""
        self._bot_stop_credits += 1
        await self._drain_ends()

    async def _drain_ends(self) -> None:
        """Emit deferred audio.end events in turn order.

        Head-of-queue entries that produced bot audio wait for a bot-stop credit;
        silent entries drain immediately. Processing strictly in order keeps a
        later turn from ending the session before an earlier reply has played.
        """
        while self._pending_ends and not self._ended_notified:
            follow_up, has_audio = self._pending_ends[0]
            if has_audio:
                if self._bot_stop_credits <= 0:
                    break
                self._bot_stop_credits -= 1
            self._pending_ends.popleft()
            await self._send_audio_end(follow_up)
            if not follow_up:
                # Session is ending; anything still queued is moot.
                self._pending_ends.clear()
                break

    async def _send_audio_end(self, follow_up: bool) -> None:
        if not follow_up:
            self._ended_notified = True
        await _send_json(
            _device_ws(self.device_id), {"type": "audio.end", "follow_up": follow_up}
        )
        registry.set_state(self.device_id, "listening" if follow_up else "idle")
        if not follow_up:
            log.info("realtime[%s]: follow_up=false — ending realtime session", self.device_id)
            # Small grace for the final WebRTC audio packets to land before
            # teardown; the firmware also tears down on audio.end{follow_up:false}.
            asyncio.create_task(self._deferred_close())

    async def _notify_ended(self) -> None:
        """Relay a terminal audio.end{follow_up:false} for an abnormal teardown.

        Normal turns end via _send_audio_end. This covers the cases where that
        never fires: a peer drop, or a deferred follow_up that's still queued
        because TTS never reached BotStoppedSpeaking (playback error/interrupt/
        disconnect). Guarded so it sends at most once, and drops any pending
        follow_up that will now never be drained.
        """
        if self._ended_notified:
            return
        self._ended_notified = True
        self._pending_ends.clear()
        self._bot_stop_credits = 0
        await _send_json(
            _device_ws(self.device_id), {"type": "audio.end", "follow_up": False}
        )
        registry.set_state(self.device_id, "idle")

    async def _deferred_close(self) -> None:
        await asyncio.sleep(0.5)
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        log.info("realtime[%s]: closing session", self.device_id)
        if self._preroll_task is not None:
            self._preroll_task.cancel()
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
        # Per-device "device finished sending pre-roll" signal. Kept on the
        # manager (not the session) so it survives the media_ready WS message
        # arriving before the /api/offer connection registers the session.
        self._media_ready: dict[str, asyncio.Event] = {}
        # Per-device lock serializing /api/offer handling: two overlapping offers
        # for the same device would otherwise both start a Pipecat runner, with
        # only the last _sessions registration winning and the other orphaned.
        self._offer_locks: dict[str, asyncio.Lock] = {}
        self._handler: Any = None
        self._max_preroll_bytes = 16000 * 2 * 10  # 10s @ 16kHz mono

    def configure(self, channel_server: Any) -> None:
        self._channel_server = channel_server

    # --- pre-roll buffering (driven by WS realtime.start + 0x01 frames) ---

    def begin_preroll(self, device_id: str) -> None:
        self._preroll[device_id] = bytearray()
        # Fresh signal for this wake — a stale set() from a prior turn must not
        # make the next session flush before the device is done sending.
        self._media_ready[device_id] = asyncio.Event()
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
        # Only drop the session. Pre-roll and the media_ready signal are armed
        # per-wake by begin_preroll and consumed by take_preroll; closing a stale
        # session (e.g. when a new offer supersedes it) must NOT wipe the pre-roll
        # that the new wake just armed, or the next turn's first utterance is lost.
        self._sessions.pop(device_id, None)

    async def abort_wake(self, device_id: str) -> None:
        """Clean up a wake whose /api/offer never produced a usable session.

        realtime.start armed pre-roll and put the device in listening; if the
        offer raises or yields no SDP answer, relay a terminal audio.end so the
        device leaves listening even if its own realtime.stop is lost, and clear
        the armed server state.
        """
        session = self._sessions.get(device_id)
        if session is not None:
            await session.close()
        self._preroll.pop(device_id, None)
        self._media_ready.pop(device_id, None)
        ws = _device_ws(device_id)
        if ws is not None:
            await _send_json(ws, {"type": "audio.end", "follow_up": False})
        registry.set_state(device_id, "idle")

    async def stop(self, device_id: str) -> None:
        session = self._sessions.get(device_id)
        if session is not None:
            await session.close()
        # Clear any pre-roll/media_ready armed for this device. stop() runs on
        # realtime.stop and on disconnect — including the case where the device
        # armed pre-roll but never completed WebRTC (no session to close), which
        # would otherwise leak the buffer/event until the next wake.
        self._preroll.pop(device_id, None)
        self._media_ready.pop(device_id, None)
        # Reset registry state: tearing the session down on realtime.stop (or a
        # bare disconnect) otherwise leaves the device showing "listening" after
        # realtime has ended, skewing HTTP status and registry-keyed logic.
        registry.set_state(device_id, "idle")

    def can_accept_offer(self, device_id: str) -> bool:
        """Whether an /api/offer for this device_id is tied to a real wake.

        An armed pre-roll means the device just sent an authenticated
        realtime.start on its WebSocket; an existing session covers re-offers
        (ICE restart / renegotiation). Without this, any LAN client holding the
        shared device token could POST an offer for an arbitrary id and attach
        WebRTC to that device's pre-roll and control relay.
        """
        return device_id in self._preroll or device_id in self._sessions

    def media_ready_event(self, device_id: str) -> asyncio.Event:
        """Get-or-create the per-device pre-roll-done signal."""
        ev = self._media_ready.get(device_id)
        if ev is None:
            ev = asyncio.Event()
            self._media_ready[device_id] = ev
        return ev

    def media_ready(self, device_id: str) -> None:
        """Device signalled realtime.media_ready — flag it.

        Set on the manager rather than poking the session directly: media_ready
        can arrive before the session is registered, and the session's flush
        task waits on this event, so the signal is never lost.
        """
        self.media_ready_event(device_id).set()

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
            # A re-offer for a device that already has a live session would
            # otherwise orphan the previous Pipecat runner + WebRTC connection.
            existing = self._sessions.get(device_id)
            if existing is not None:
                log.info("realtime[%s]: closing previous session before new offer", device_id)
                await existing.close()
            session = RealtimeSession(device_id, self._channel_server)
            # Register only after start() succeeds: a failed start would otherwise
            # leave a half-built session in _sessions, which later offers (and
            # can_accept_offer) would treat as live.
            try:
                await session.start(connection)
            except Exception:
                log.exception("realtime[%s]: session start failed", device_id)
                await session.close()
                raise
            self._sessions[device_id] = session

        # Serialize per device so two overlapping offers can't both build a
        # session (and leak a runner) for the same device_id.
        lock = self._offer_locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            return await handler.handle_web_request(req, _on_connection)


_manager: RealtimeManager | None = None


def get_manager() -> RealtimeManager:
    global _manager
    if _manager is None:
        _manager = RealtimeManager()
    return _manager
