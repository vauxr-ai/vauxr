"""Browser GPT-Live pipeline using the pinned Pipecat service and worker contract."""
from __future__ import annotations

import asyncio
import os
import secrets
from typing import Any

from pipecat.bus import BusJobRequestMessage
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.job_decorator import job
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.openai.live.llm import ClientDelegation, OpenAILiveLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.base_worker import BaseWorker
from pipecat.workers.runner import WorkerRunner

import agent_registry
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
        await self.live.flush_transcript()
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
        self.agent_id = agent_id
        self.session_id = secrets.token_hex(16)
        self.fragments: list[dict[str, object]] = []
        self.sequence = 0
        self.flush_lock = asyncio.Lock()
        self.flush_task: asyncio.Task | None = None
        super().__init__(api_key=os.environ["OPENAI_API_KEY"],
                         settings=self.Settings(model=settings["realtime_model"], voice=settings["realtime_voice"]),
                         delegation=ClientDelegation(backend=BackendWorker(self), timeout_secs=300))

    async def request(self, operation: str, payload: dict | None = None, *, timeout: float = 30) -> dict:
        return await self.session._agent_server.realtime_request(
            self.agent_id, self.session.device_id, self.session_id, operation, payload, timeout=timeout,
        )

    async def _handle_evt_transcript_delta(self, evt: Any) -> None:
        if evt.delta:
            self.sequence += 1
            self.fragments.append({"id": str(self.sequence), "role": evt.role, "text": evt.delta,
                                   "delivered": False})
            self.session._touch_activity()
            await self.session._send_control({"type": "realtime.transcript", "role": evt.role, "text": evt.delta})
            if self.flush_task is None or self.flush_task.done():
                self.flush_task = asyncio.create_task(self._flush_later())
        await super()._handle_evt_transcript_delta(evt)

    async def _flush_later(self) -> None:
        await asyncio.sleep(0.5)
        try:
            await self.flush_transcript()
        except Exception:
            await self.session._send_control({"type": "error", "code": "REALTIME_HISTORY_FAILED",
                "message": "Backend conversation could not be saved. Stop and check the Agent connection."})
            asyncio.create_task(self.session.close())

    async def finish_transcript(self) -> None:
        try:
            await asyncio.wait_for(self.flush_transcript(), timeout=1.5)
        except (Exception, asyncio.CancelledError):
            # A revoked/offline backend cannot acknowledge history. This must
            # not hold revoked media authority alive or prevent provider close.
            from realtime_session import _device_ws, _send_json
            await _send_json(_device_ws(self.session.device_id), {"type": "error",
                "code": "REALTIME_HISTORY_FAILED", "message": "Some voice history could not be saved to the backend."})

    async def flush_transcript(self) -> None:
        async with self.flush_lock:
            while self.fragments:
                batch = self.fragments[:64]
                await self.request("record", {"fragments": batch})
                del self.fragments[:len(batch)]


async def start_live(session: Any, connection: Any) -> None:
    active = agent_registry.get_active()
    if active is None or active.type != "openclaw":
        raise ValueError("Realtime requires a selected OpenClaw integration Agent")
    session._connection = connection
    llm = LiveService(session, active.id, get_store().voice_settings(session.device_id))
    bootstrap = await llm.request("bootstrap")
    instructions = bootstrap.get("instructions", "")
    messages = bootstrap.get("messages", [])
    if not isinstance(instructions, str) or len(instructions) > 16000 or not isinstance(messages, list):
        raise ValueError("Invalid backend realtime context")
    context = LLMContext(messages=[{"role": "system", "content": LIVE_INSTRUCTIONS + "\n" + instructions},
                                   *messages])
    user, assistant = LLMContextAggregatorPair(context)
    transport = SmallWebRTCTransport(webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True))
    session._live_service = llm
    session._context = context
    session._task = PipelineWorker(Pipeline([transport.input(), user, llm, transport.output(), assistant]),
        params=PipelineParams(audio_in_sample_rate=24000, audio_out_sample_rate=24000))
    session._runner = WorkerRunner(handle_sigint=False)

    @transport.event_handler("on_client_connected")
    async def connected(_transport: Any, _connection: Any) -> None:
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
