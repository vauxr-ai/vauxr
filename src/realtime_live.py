"""Browser GPT-Live pipeline using the pinned Pipecat service and worker contract."""
from __future__ import annotations

import asyncio
import os
import secrets
import time
from typing import Any

from pipecat.bus import BusJobRequestMessage
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMRunFrame,
    OutputAudioRawFrame,
    SpeechOutputAudioRawFrame,
)
from pipecat.pipeline.job_decorator import job
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.live import events as live_events
from pipecat.services.openai.live.llm import ClientDelegation, OpenAILiveLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.base_worker import BaseWorker
from pipecat.workers.runner import WorkerRunner

import agent_registry
from config import get_config
from realtime_audio_diagnostics import LiveAudioDiagnostics
from realtime_gain import apply_gain, db_to_linear
from realtime_transcript import TranscriptRelay
from speech import get_store

LIVE_INSTRUCTIONS = (
    "Handle casual voice conversation naturally. Consult the backend for personal memory, "
    "context you lack, deeper reasoning and all actions. Never claim an action succeeded without "
    "its backend result. Backend results are context for your spoken reply, not another voice. "
    "If interrupted, listen to the correction. An action may continue after speech stops. "
    "Do not repeat an action already completed."
)


class BackendWorker(BaseWorker):
    def __init__(self, live: LiveService) -> None:
        super().__init__()
        self.live = live

    @job(name="run", sequential=True)
    async def consult(self, message: BusJobRequestMessage) -> None:
        await self.live.flush_transcript(wait_for_user=True)
        request = "Consult the latest outstanding request in this realtime conversation."
        result = await self.live.request("consult", {"request": request}, timeout=300)
        text = str(result.get("text", ""))
        await self.send_job_update(message.job_id, {"text": text, "is_final": True, "prefers_spoken": True})
        await self.send_job_response(message.job_id, {"text": text})


class LiveService(OpenAILiveLLMService):
    """Keep Pipecat's audio/interruption/delegation behavior; mirror transcript separately.

    The transcript callback is a pinned 1.9.0 integration seam. Its timed fragments
    describe generated output, not browser playback receipts, so assistant records
    conservatively retain unconfirmed-delivery metadata.
    """
    def __init__(self, session: Any, agent_id: str, settings: dict[str, str]) -> None:
        self.session = session
        from realtime_device_activity import DeviceActivity
        self.device_activity = DeviceActivity(session) if getattr(session, "_handoff_pending", False) else None
        self.agent_id = agent_id
        self.session_id = secrets.token_hex(16)
        self.fragments: list[dict[str, object]] = []
        self.turn_sequence = 0
        self.transcript_turns: dict[str, str] = {}
        self.user_turn_done = asyncio.Event()
        self.user_turn_done.set()
        self.finishing = False
        self.flush_lock = asyncio.Lock()
        self.flush_task: asyncio.Task | None = None
        self.transcript_relay = TranscriptRelay(lambda message: session._send_control(message), self._transcript_failed)
        self.audio_diagnostics = (LiveAudioDiagnostics(self.transcript_relay)
                                  if os.environ.get("REALTIME_AUDIO_DIAGNOSTICS") == "1" else None)
        super().__init__(api_key=os.environ["OPENAI_API_KEY"],
                         settings=self.Settings(model=settings["realtime_model"], voice=settings["realtime_voice"]),
                         delegation=ClientDelegation(backend=BackendWorker(self), timeout_secs=300))

    async def _handle_server_event(self, evt: live_events.ServerEvent) -> None:
        activity = self.device_activity
        if activity:
            if evt.type == "session.started":
                activity.ready = True
            elif evt.type in ("session.closed", "error"):
                await activity.stop()
            elif isinstance(evt, live_events.SessionDelegationCreatedEvent):
                await activity.delegation(evt.delegation.id, True)
            elif isinstance(evt, live_events.ResponseEventEnvelope):
                if evt.inner_type in ("response.completed", "response.incomplete", "response.failed", "response.cancelled"):
                    await activity.delegation(evt.delegation_id, False)
        diagnostic = self.audio_diagnostics
        if diagnostic is None or not diagnostic.active:
            await super()._handle_server_event(evt)
            return
        diagnostic.event(evt.type)
        if isinstance(evt, live_events.ResponseEventEnvelope):
            diagnostic.event(evt.inner_type, nested=True)
        diagnostic.handler_started = time.monotonic()
        try:
            await super()._handle_server_event(evt)
        except Exception:
            diagnostic.count("provider_handler_error")
            raise
        finally:
            diagnostic.handler_max_ms = max(diagnostic.handler_max_ms,
                (time.monotonic() - diagnostic.handler_started) * 1000)
            diagnostic.handler_started = None

    async def send_client_event(self, event: live_events.ClientEvent) -> None:
        if self.audio_diagnostics:
            self.audio_diagnostics.event(event.type, outgoing=True)
        await super().send_client_event(event)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if self.audio_diagnostics and isinstance(frame, InputAudioRawFrame):
            self.audio_diagnostics.count("input_frames_at_service")
        if isinstance(frame, InputAudioRawFrame) and getattr(self.session, "_handoff_pending", False):
            if self.audio_diagnostics:
                self.audio_diagnostics.count("input_blocked_handoff")
            return
        if self.device_activity:
            if isinstance(frame, InputAudioRawFrame):
                await self.device_activity.input_audio(frame)
            elif isinstance(frame, InterruptionFrame):
                await self.device_activity.interrupt()
            elif isinstance(frame, (CancelFrame, EndFrame)):
                self.device_activity.close()
        if self.audio_diagnostics and isinstance(frame, InputAudioRawFrame):
            self.audio_diagnostics.pcm("mic", frame.audio, frame.sample_rate)
            if not self._session_started:
                self.audio_diagnostics.count("input_waiting_provider")
        await super().process_frame(frame, direction)

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if self.device_activity and isinstance(frame, SpeechOutputAudioRawFrame):
            await self.device_activity.provider_audio(frame.audio)
        if self.audio_diagnostics and isinstance(frame, SpeechOutputAudioRawFrame):
            self.audio_diagnostics.pcm("provider", frame.audio, frame.sample_rate)
        await super().push_frame(frame, direction)

    async def request(self, operation: str, payload: dict | None = None, *, timeout: float = 30) -> dict:
        return await self.session._agent_server.realtime_request(
            self.agent_id, self.session.device_id, self.session_id, operation, payload, timeout=timeout,
        )

    async def _handle_evt_transcript_delta(self, evt: Any) -> None:
        if self.finishing:
            return
        if evt.delta:
            self.session._touch_activity()
            if self.device_activity:
                await self.device_activity.transcript(evt.role, evt.delta)
        await super()._handle_evt_transcript_delta(evt)

    async def _open_turn(self, role: str) -> None:
        if role == "user":
            self.user_turn_done.clear()
        self.turn_sequence += 1
        self.transcript_turns[role] = f"{self.session_id}-{self.turn_sequence}"
        await super()._open_turn(role)

    async def _append_turn(self, role: str, delta: str, accumulated: str, evt: Any) -> None:
        # Keep Pipecat's audio/context frames streaming. Browser text is a
        # replaceable snapshot, never another persistent history fragment.
        await super()._append_turn(role, delta, accumulated, evt)
        await self._send_transcript_turn(role, accumulated, final=False)

    async def _end_turn(self, role: str) -> None:
        # Pipecat 1.9.0 Live has timed deltas, not transcript completed events.
        # Reuse its per-speaker quiet-gap boundary under the existing turn lock.
        turn = self._user_turn if role == "user" else self._assistant_turn
        text = turn.text if turn.open else ""
        await super()._end_turn(role)
        if text:
            # The SDK transcript writer appends immutable, idempotent messages.
            # Only a closed turn is a record; UI snapshots/deltas are not records.
            self.fragments.append({"id": self.transcript_turns[role], "role": role,
                                   "text": text, "delivered": False})
            if not self.finishing and (self.flush_task is None or self.flush_task.done()):
                self.flush_task = asyncio.create_task(self._flush_later())
            await self._send_transcript_turn(role, text, final=True)
        self.transcript_turns.pop(role, None)
        if role == "user":
            self.user_turn_done.set()

    async def _send_transcript_turn(self, role: str, text: str, *, final: bool) -> None:
        self.transcript_relay.enqueue(role, text, self.transcript_turns[role], final)

    def _transcript_failed(self) -> None:
        if not self.finishing:
            asyncio.create_task(self.session.close())

    async def _run_client_delegation(self, delegation: Any) -> None:
        try:
            await super()._run_client_delegation(delegation)
        finally:
            if self.device_activity:
                await self.device_activity.delegation(delegation.id, False)

    async def cleanup(self) -> None:
        if self.device_activity:
            self.device_activity.close()
        await self.transcript_relay.close()
        if self.audio_diagnostics:
            await self.audio_diagnostics.close()
        await super().cleanup()

    async def _flush_later(self) -> None:
        await asyncio.sleep(0.5)
        try:
            await self.flush_transcript()
        except Exception:
            await self.session._send_control({"type": "error", "code": "REALTIME_HISTORY_FAILED",
                "message": "Backend conversation could not be saved. Stop and check the Agent connection."})
            asyncio.create_task(self.session.close())

    async def finish_transcript(self) -> None:
        self.finishing = True
        if self.device_activity:
            self.device_activity.close()

        async def finish() -> None:
            # Seal partial output once, before the provider/pipeline is cancelled.
            await self._close_open_turns()
            await self.flush_transcript()

        try:
            await asyncio.wait_for(finish(), timeout=1.5)
        except (Exception, asyncio.CancelledError):
            # A revoked/offline backend cannot acknowledge history. This must
            # not hold revoked media authority alive or prevent provider close.
            from realtime_session import _device_ws, _send_json
            await _send_json(_device_ws(self.session.device_id), {"type": "error",
                "code": "REALTIME_HISTORY_FAILED", "message": "Some voice history could not be saved to the backend."})
        finally:
            await self.transcript_relay.close(drain=True)
            if self.audio_diagnostics:
                self.audio_diagnostics.count("application_finish")
                await self.audio_diagnostics.close()

    async def release(self) -> None:
        """Release the plugin's transient connection scope without touching history."""
        try:
            await asyncio.wait_for(self.request("release"), timeout=1.5)
        except (Exception, asyncio.CancelledError):
            # The plugin may already be disconnected. Server-side teardown must
            # still finish; a later bootstrap creates a fresh transient scope.
            pass

    async def flush_transcript(self, *, wait_for_user: bool = False) -> None:
        if wait_for_user:
            # Delegation may precede Pipecat's quiet-gap final. Do not dispatch
            # without the request in history, or manufacture a mid-sentence turn.
            # This worker wait does not block provider audio or transcript frames.
            await self.user_turn_done.wait()
        async with self.flush_lock:
            while self.fragments:
                batch = self.fragments[:64]
                await self.request("record", {"fragments": batch})
                del self.fragments[:len(batch)]


class OutputGain(FrameProcessor):
    """Scale spoken PCM leaving the Live service so it matches Standard-mode loudness."""

    def __init__(self, gain_db: float) -> None:
        super().__init__()
        self.gain = db_to_linear(gain_db)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            frame.audio = apply_gain(frame.audio, self.gain)
        await self.push_frame(frame, direction)


async def start_live(session: Any, connection: Any) -> None:
    active = agent_registry.get_active()
    if active is None or active.type != "openclaw":
        raise ValueError("Realtime requires a selected OpenClaw integration Agent")
    session._connection = connection
    if getattr(session, "_handoff_pending", False):
        if get_config().realtime.esp32_mode:
            from realtime_transport import use_websocket_control
            use_websocket_control(connection)
    llm = LiveService(session, active.id, get_store().voice_settings(session.device_id))
    # Bootstrap may create remote scope even when its reply fails or is invalid.
    # Give session teardown ownership before the first remote request.
    session._live_service = llm
    bootstrap = await llm.request("bootstrap")
    instructions = bootstrap.get("instructions", "")
    messages = bootstrap.get("messages", [])
    if not isinstance(instructions, str) or len(instructions) > 16000 or not isinstance(messages, list):
        raise ValueError("Invalid backend realtime context")
    if getattr(session, "_handoff_pending", False):
        # Pipecat interprets trailing developer history as a request to speak.
        # Retain backend context as instructions without replaying completed work.
        messages = list(messages)
        trailing = []
        while messages and messages[-1].get("role") == "developer":
            trailing.insert(0, {**messages.pop(), "role": "system"})
        messages = [*trailing, *messages]
    context = LLMContext(messages=[{"role": "system", "content": LIVE_INSTRUCTIONS + "\n" + instructions},
                                   *messages])
    user, assistant = LLMContextAggregatorPair(context)
    transport = SmallWebRTCTransport(webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True))
    if llm.device_activity:
        llm.device_activity.bind_output(transport.output())
        llm.device_activity.start()
    rtp_probes = None
    if llm.audio_diagnostics:
        from realtime_rtp_diagnostics import install
        if getattr(session, "_handoff_pending", False):
            rtp_probes = install(connection)
            session._rtp_probes = rtp_probes
        llm.audio_diagnostics.bind_output(transport.output())
    session._context = context
    gain = OutputGain(get_config().realtime.output_gain_db)
    session._task = PipelineWorker(Pipeline([transport.input(), user, llm, gain, transport.output(), assistant]),
        params=PipelineParams(audio_in_sample_rate=24000, audio_out_sample_rate=24000))
    session._runner = WorkerRunner(handle_sigint=False)

    @transport.event_handler("on_client_connected")
    async def connected(_transport: Any, _connection: Any) -> None:
        if llm.audio_diagnostics:
            llm.audio_diagnostics.bind_track(transport.output()._client._audio_output_track)
        if llm.device_activity:
            llm.device_activity.bind_track(transport.output()._client._audio_output_track)
        session._pipeline_ready.set()
        await session._task.queue_frame(LLMRunFrame())

    @transport.event_handler("on_client_disconnected")
    async def disconnected(_transport: Any, _connection: Any) -> None:
        asyncio.create_task(session.close())

    @llm.event_handler("on_session_started")
    async def ready(_service: Any, _provider_session: Any) -> None:
        await session._send_control({"type": "realtime.ready"})

    @session._task.event_handler("on_pipeline_error")
    async def failed(_worker: Any, _frame: Any) -> None:
        await session._send_control({"type": "error", "code": "REALTIME_PROVIDER_ERROR",
            "message": "Realtime failed. Check the server OpenAI key, gpt-live-1 access and Agent connection. "
                       "Backend actions may continue; check their status before retrying."})
        asyncio.create_task(session.close())

    async def run() -> None:
        try:
            await session._runner.add_workers(session._task)
            await session._runner.run()
        finally:
            if not session._closed:
                asyncio.create_task(session.close())

    session._runner_task = asyncio.create_task(run())
    session._backstop_task = asyncio.create_task(session._safety_backstop())
    if llm.audio_diagnostics:
        from realtime_rtp_diagnostics import monitor
        llm.audio_diagnostics.start()
        session._rtp_diag_task = asyncio.create_task(monitor(connection, session.device_id, rtp_probes or []))
