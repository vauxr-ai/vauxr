#!/usr/bin/env python3
"""Vauxr Pipecat realtime PoC — interruptible WebRTC voice bot.

Standalone spike: does NOT touch vauxr WS protocol, pipeline.py, or firmware.
Run this to feel full-duplex barge-in + streaming before deciding on hybrid
transport switching.

Usage (from this directory):
    cp .env.example .env   # add OPENAI_API_KEY or LLM_BASE_URL
    python -m venv .venv && source .venv/bin/activate
    pip install -e .
    python bot.py -t webrtc --host 0.0.0.0

Browser:  http://<your-ip>:7860/client
ESP32:    python bot.py -t webrtc --esp32 --host <your-lan-ip>
          PIPECAT_SMALLWEBRTC_URL=http://<ip>:7860/api/offer  (pipecat-esp32)
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

import asyncio


def _broaden_aiortc_dtls_ciphers() -> None:
    """Let aiortc accept ECDHE-RSA DTLS suites, not just ECDHE-ECDSA.

    aiortc >=1.x hardcodes an ECDSA-only DTLS cipher list. Espressif's esp_peer
    (the firmware WebRTC stack) presents an RSA DTLS certificate, so with stock
    aiortc there is no common ciphersuite and the DTLS handshake dies with
    HANDSHAKE_FAILURE before any media flows. Adding the ECDHE-RSA suites back
    makes the ESP32 connect; browsers already support them, so this is harmless
    for the web client.
    """
    from aiortc.rtcdtlstransport import RTCCertificate

    if getattr(RTCCertificate, "_vauxr_cipher_patched", False):
        return

    _orig = RTCCertificate._create_ssl_context
    _ciphers = (
        b"ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
        b"ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:"
        b"ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES128-SHA:"
        b"ECDHE-ECDSA-AES256-SHA:ECDHE-RSA-AES256-SHA:"
        b"AES128-GCM-SHA256:AES128-SHA:AES256-SHA"
    )

    def _patched(self, srtp_profiles):
        ctx = _orig(self, srtp_profiles)
        ctx.set_cipher_list(_ciphers)
        return ctx

    RTCCertificate._create_ssl_context = _patched
    RTCCertificate._vauxr_cipher_patched = True
    logger.info("Patched aiortc DTLS cipher list to include ECDHE-RSA (esp_peer compat)")


_broaden_aiortc_dtls_ciphers()

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    LLMRunFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import TransportParams

VOICE_SYSTEM = (
    "You are a helpful voice assistant on a smart speaker. "
    "Responses are spoken aloud — no emojis, markdown, code blocks, or URLs. "
    "Use short, natural sentences. Be concise."
)


class VADStopUserTurnStopStrategy(BaseUserTurnStopStrategy):
    """End the user turn when the batch transcript lands after VAD stop.

    Wyoming/Whisper is segmented: SegmentedSTTService kicks off transcription
    on VAD stop and emits the TranscriptionFrame ~0.5-1s later. We therefore
    finalize the turn when that transcript arrives (not on raw VAD stop) —
    otherwise the turn closes empty and the LLM runs one turn behind. A short
    fallback timeout finalizes anyway if no transcript shows up (pure silence
    / a noise blip dropped upstream), so the turn machine never wedges.

    The pipecat defaults (LocalSmartTurnAnalyzerV3, SpeechTimeoutUserTurn-
    StopStrategy) assume streaming STT and don't fit this batch flow.
    """

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


def _build_stt():
    backend = os.environ.get("POC_BACKEND", "wyoming").lower()
    if backend == "wyoming":
        from wyoming_services import WyomingSTTService

        return WyomingSTTService()
    raise ValueError(f"Unknown POC_BACKEND={backend!r} — use 'wyoming'")


def _build_tts():
    backend = os.environ.get("POC_BACKEND", "wyoming").lower()
    if backend == "wyoming":
        from wyoming_services import WyomingTTSService

        return WyomingTTSService()
    if backend == "piper_embedded":
        from pipecat.services.piper.tts import PiperTTSService

        voice = os.environ.get("PIPER_VOICE", "en_US-libritts_r-medium")
        return PiperTTSService(settings=PiperTTSService.Settings(voice=voice))
    raise ValueError(f"Unknown POC_BACKEND={backend!r}")


def _build_llm() -> OpenAILLMService:
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("Set OPENAI_API_KEY or LLM_API_KEY in .env")
        sys.exit(1)

    base_url = os.environ.get("LLM_BASE_URL")
    model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

    kwargs: dict = {
        "api_key": api_key,
        "settings": OpenAILLMService.Settings(
            model=model,
            system_instruction=VOICE_SYSTEM,
            temperature=0.7,
            max_completion_tokens=300,
        ),
    }
    if base_url:
        kwargs["base_url"] = base_url

    return OpenAILLMService(**kwargs)


async def bot(runner_args: RunnerArguments) -> None:
    """Pipecat runner entry point — called once per WebRTC session."""
    transport = await create_transport(
        runner_args,
        {
            "webrtc": lambda: TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        },
    )

    stt = _build_stt()
    tts = _build_tts()
    llm = _build_llm()

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # Stricter VAD to reject background-noise false triggers.
            # stop_secs is the only end-of-turn latency knob now: the turn
            # closes this many seconds after you go quiet.
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    confidence=0.7,
                    start_secs=0.2,
                    stop_secs=0.6,
                    min_volume=0.6,
                )
            ),
            # Pure VAD turn-taking. Start when VAD hears speech, stop when VAD
            # hears silence — no ML smart-turn model and no transcript gating,
            # both of which deadlock with batch Whisper STT (see
            # VADStopUserTurnStopStrategy).
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[VADStopUserTurnStopStrategy()],
            ),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client) -> None:
        logger.info("Client connected — say something (try interrupting mid-reply)")
        # Optional greeting turn; comment out if you prefer silence until user speaks.
        context.add_message({"role": "user", "content": "The user just connected. Greet them briefly."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client) -> None:
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
