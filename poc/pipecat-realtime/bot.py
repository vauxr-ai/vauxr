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

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import TransportParams

VOICE_SYSTEM = (
    "You are a helpful voice assistant on a smart speaker. "
    "Responses are spoken aloud — no emojis, markdown, code blocks, or URLs. "
    "Use short, natural sentences. Be concise."
)


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
            vad_analyzer=SileroVADAnalyzer(),
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
