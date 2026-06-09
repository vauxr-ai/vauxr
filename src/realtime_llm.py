"""Pipecat LLM service that routes through vauxr's channel plugin.

Instead of calling a cloud LLM, this service hands the user's transcript to the
active channel (via `ChannelServer.send_transcript`) and streams the channel's
`channel.response.*` deltas back into the pipeline as `LLMTextFrame`s for TTS.

This is what makes the realtime/WebRTC path share the *same* agent, sessions,
and `follow_up` semantics as the turn-based WS pipeline (`pipeline._route_via_channel`).
The `[[follow_up]]` tag is stripped from spoken text; the resolved follow_up
boolean is reported via `on_turn_complete` so the session can decide whether to
stay in realtime mode or return the device to silent wake-waiting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMContextFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from channel_server import ChannelServer
from pipeline import (
    _follow_up_mode_for,
    resolve_follow_up,
    strip_follow_up_tag_inline,
)

_RESPONSE_TIMEOUT_S = 120.0

# Callback: (follow_up, reply_text) -> awaitable | None, fired after each turn.
TurnCompleteCb = Callable[[bool, str], Any]


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


def _count_user_messages(context: Any) -> int:
    """Number of user-role messages in the context.

    A genuinely new utterance appends a new user message; a VAD silence-fallback
    re-finalize of the same turn does not. Counting (rather than comparing text)
    lets us skip the re-finalize without dropping a legitimately repeated phrase.
    """
    try:
        messages = context.get_messages()
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for m in messages if m.get("role") == "user")


class ChannelLLMService(LLMService):
    """Route LLM turns through the active vauxr channel plugin."""

    def __init__(
        self,
        *,
        device_id: str,
        channel_server: ChannelServer,
        on_turn_complete: TurnCompleteCb | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._device_id = device_id
        self._channel_server = channel_server
        self._on_turn_complete = on_turn_complete
        # User-message count at the last turn we routed. VADStopUserTurnStopStrategy
        # can finalize a turn on its silence fallback without a new transcript; in
        # that case the count is unchanged and we skip re-sending the prior
        # utterance — while still allowing a legitimately repeated phrase (e.g.
        # "yes" twice), which appends a new user message and bumps the count.
        self._last_user_msg_count = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self._run_turn(frame.context)
        else:
            await self.push_frame(frame, direction)

    async def _run_turn(self, context: Any) -> None:
        text = _latest_user_text(context)
        if not text:
            # Empty transcript (VAD blip, dropped STT, noise). Skip the turn but
            # stay in the realtime session — ending here on follow_up=false would
            # kick the user out of multi-turn listening on a spurious trigger.
            logger.warning("ChannelLLM: empty user text — skipping turn, staying in session")
            await self._emit_empty_response()
            return

        user_count = _count_user_messages(context)
        if user_count <= self._last_user_msg_count:
            # No new user message since the last turn (VAD silence fallback re-
            # finalizing the same turn); the context still holds the prior
            # utterance. Skip rather than re-send. A real repeat of the same
            # phrase appends a new user message, so user_count would advance.
            logger.warning("ChannelLLM: no new user message since last turn — skipping")
            await self._emit_empty_response()
            return
        self._last_user_msg_count = user_count

        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()

        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        def on_delta(_run_id: str, delta: str) -> None:
            queue.put_nowait(("delta", delta))

        def on_end(_run_id: str) -> None:
            queue.put_nowait(("end", ""))

        def on_error(_run_id: str, message: str) -> None:
            queue.put_nowait(("error", message))

        listener = {"on_delta": on_delta, "on_end": on_end, "on_error": on_error}
        self._channel_server.add_response_listener(self._device_id, listener)

        # Realtime routes only through the channel plugin. send_transcript returns
        # false for openclaw-direct (operator) mode, which realtime does not yet
        # support — the turn-based WS pipeline handles that case instead.
        sent = self._channel_server.send_transcript(self._device_id, text)
        accumulated = ""
        error: str | None = None
        try:
            if not sent:
                error = "Active channel not connected (openclaw-direct is not supported in realtime)"
            else:
                while True:
                    try:
                        kind, payload = await asyncio.wait_for(
                            queue.get(), timeout=_RESPONSE_TIMEOUT_S
                        )
                    except asyncio.TimeoutError:
                        error = f"Channel response timeout after {int(_RESPONSE_TIMEOUT_S)}s"
                        break
                    if kind == "delta":
                        accumulated += payload
                        spoken = strip_follow_up_tag_inline(payload)
                        if spoken:
                            await self.push_frame(LLMTextFrame(spoken))
                    elif kind == "end":
                        break
                    elif kind == "error":
                        error = payload
                        break
        finally:
            self._channel_server.remove_response_listener(self._device_id, listener)
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())

        if error:
            logger.error("ChannelLLM error for {}: {}", self._device_id, error)
            await self.push_error(ErrorFrame(error))
            if self._on_turn_complete:
                await self._maybe_await(self._on_turn_complete(False, ""))
            return

        result = resolve_follow_up(accumulated, _follow_up_mode_for(self._device_id))
        logger.info(
            "ChannelLLM reply ({} chars, follow_up={}): {}",
            len(result.reply_text),
            result.follow_up,
            result.reply_text[:160],
        )
        if self._on_turn_complete:
            await self._maybe_await(self._on_turn_complete(result.follow_up, result.reply_text))

    async def _emit_empty_response(self) -> None:
        """Emit a balanced, empty LLM response for a skipped turn.

        Downstream processors (the assistant context aggregator, turn-taking)
        pair every handled LLMContextFrame with an LLMFullResponseStart/End span.
        Returning without them can leave the aggregator stuck mid-response and
        stall subsequent WebRTC turns. We deliberately do NOT call
        on_turn_complete: a skipped turn has no bot speech, so emitting audio.end
        would drop the user out of the realtime session on a spurious trigger.
        """
        await self.push_frame(LLMFullResponseStartFrame())
        await self.push_frame(LLMFullResponseEndFrame())

    @staticmethod
    async def _maybe_await(value: Any) -> None:
        if asyncio.iscoroutine(value):
            await value
