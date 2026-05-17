"""Voice-turn pipeline — STT → LLM (direct or channel) → TTS → device audio.

Port of `src/pipeline.ts`. The two routing modes (openclaw-direct vs.
channel plugin) keep the Node port's exact behavior: direct mode uses the
final cumulative delta as the full reply; channel mode accumulates
incremental deltas through the idle segmenter and TTS queue.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import device_registry as registry
from .config import get_config
from .device_config import FollowUpMode
from .idle_segmenter import IdleSegmenter
from .protocol import encode_text_message
from .segment_queue import SegmentQueue
from .utils import make_binary_frame
from .wyoming_stt import transcribe
from .wyoming_tts import synthesize

if TYPE_CHECKING:
    from aiohttp.web import WebSocketResponse

    from .channel_server import ChannelServer
    from .openclaw_client import OpenClawClient

log = logging.getLogger("vauxr.pipeline")

_FOLLOW_UP_TAG = "[[follow_up]]"
_FOLLOW_UP_RE = re.compile(r"\s*\[\[follow_up\]\]\s*")
_CHANNEL_RESPONSE_TIMEOUT_S = 120.0
_ERROR_FALLBACK_TEXT = "Sorry, I couldn't reach the backend. Please try again later."


@dataclass(frozen=True)
class FollowUpResult:
    follow_up: bool
    reply_text: str


def strip_follow_up_tag(text: str) -> str:
    return _FOLLOW_UP_RE.sub(" ", text).strip()


def strip_follow_up_tag_inline(text: str) -> str:
    return _FOLLOW_UP_RE.sub(" ", text)


def resolve_follow_up(full_reply: str, mode: FollowUpMode) -> FollowUpResult:
    trimmed = full_reply.strip()
    if mode == "always":
        return FollowUpResult(follow_up=True, reply_text=strip_follow_up_tag(trimmed))
    if mode == "never":
        return FollowUpResult(follow_up=False, reply_text=strip_follow_up_tag(trimmed))
    # "auto"
    if _FOLLOW_UP_TAG in trimmed:
        return FollowUpResult(follow_up=True, reply_text=strip_follow_up_tag(trimmed))
    trimmed_end = trimmed.rstrip()
    if trimmed_end.endswith("?") or trimmed_end.endswith("？"):
        return FollowUpResult(follow_up=True, reply_text=trimmed)
    return FollowUpResult(follow_up=False, reply_text=trimmed)


def _follow_up_mode_for(device_id: str) -> FollowUpMode:
    mode = registry.get_config_for(device_id).get("follow_up_mode")
    return mode or "auto"


# --- WS helpers (the WS type is opaque; we duck-type on .send_str / .send_bytes / .closed) ---


async def _send_json(ws: Any, obj: dict[str, Any]) -> None:
    if getattr(ws, "closed", False):
        return
    try:
        await ws.send_str(encode_text_message(obj))
    except ConnectionResetError:
        pass


async def _send_binary(ws: Any, device_id: str, msg_type: int, payload: bytes) -> None:
    if getattr(ws, "closed", False):
        return
    seq = registry.next_seq(device_id)
    try:
        await ws.send_bytes(make_binary_frame(msg_type, seq, payload))
    except ConnectionResetError:
        pass


async def _send_audio_end(ws: Any, follow_up: bool) -> None:
    await _send_json(ws, {"type": "audio.end", "follow_up": follow_up})


async def _synthesize_and_send(
    ws: Any,
    device_id: str,
    text: str,
    abort: asyncio.Event,
    target_rate: int | None,
) -> None:
    if not text:
        return
    state = {"sent_start": False}

    def on_rate(rate: int) -> None:
        if not state["sent_start"]:
            asyncio.create_task(_send_json(ws, {"type": "audio.start", "sample_rate": rate}))
            state["sent_start"] = True

    try:
        async for chunk in synthesize(text, target_rate=target_rate, abort_event=abort, on_sample_rate=on_rate):
            if abort.is_set():
                return
            await _send_binary(ws, device_id, 0x02, chunk)
    except Exception as e:  # noqa: BLE001
        log.error("TTS error: %s", e)


async def _synthesize_error_message(
    ws: Any, device_id: str, abort: asyncio.Event, target_rate: int | None
) -> None:
    state = {"sent_start": False}

    def on_rate(rate: int) -> None:
        if not state["sent_start"]:
            asyncio.create_task(_send_json(ws, {"type": "audio.start", "sample_rate": rate}))
            state["sent_start"] = True

    try:
        async for chunk in synthesize(
            _ERROR_FALLBACK_TEXT, target_rate=target_rate, abort_event=abort, on_sample_rate=on_rate
        ):
            if abort.is_set():
                return
            await _send_binary(ws, device_id, 0x02, chunk)
    except Exception as e:  # noqa: BLE001
        log.error("TTS error for error message: %s", e)


# --- Routing ---


async def _route_via_openclaw_direct(
    device_id: str,
    transcript_text: str,
    ws: Any,
    openclaw_client: "OpenClawClient",
    abort: asyncio.Event,
    target_rate: int | None,
) -> None:
    session_key = f"vauxr:{device_id}"
    full_reply = ""

    def on_delta(text: str) -> None:
        nonlocal full_reply
        # openclaw-direct sends cumulative deltas — keep the latest as the
        # full reply (matches src/pipeline.ts routeViaOpenClawDirect).
        full_reply = text

    try:
        await openclaw_client.chat(session_key, transcript_text, on_delta)
    except Exception as err:  # noqa: BLE001
        await _send_json(
            ws, {"type": "error", "code": "BACKEND_ERROR", "message": str(err)}
        )
        await _synthesize_error_message(ws, device_id, abort, target_rate)
        if not abort.is_set():
            await _send_audio_end(ws, False)
        return

    if abort.is_set():
        return

    result = resolve_follow_up(full_reply, _follow_up_mode_for(device_id))
    log.info(
        "LLM reply (%d chars, follow_up=%s): %s",
        len(result.reply_text),
        result.follow_up,
        result.reply_text[:200],
    )
    await _synthesize_and_send(ws, device_id, result.reply_text, abort, target_rate)
    if not abort.is_set():
        await _send_audio_end(ws, result.follow_up)


async def _route_via_channel(
    device_id: str,
    transcript_text: str,
    ws: Any,
    channel_server: "ChannelServer",
    abort: asyncio.Event,
    target_rate: int | None,
) -> None:
    sent = channel_server.send_transcript(device_id, transcript_text)
    if not sent:
        await _send_json(
            ws, {"type": "error", "code": "NO_CHANNEL", "message": "Active channel not connected"}
        )
        if not abort.is_set():
            await _send_audio_end(ws, False)
        return

    log.info("Awaiting channel response for %s", device_id)
    idle_pause_ms = get_config().streaming_tts.idle_pause_ms
    start_state = {"sent_start": False}

    async def synth_segment(text: str) -> None:
        if not text:
            return

        def on_rate(rate: int) -> None:
            if not start_state["sent_start"]:
                asyncio.create_task(_send_json(ws, {"type": "audio.start", "sample_rate": rate}))
                start_state["sent_start"] = True

        async for chunk in synthesize(text, target_rate=target_rate, abort_event=abort, on_sample_rate=on_rate):
            if abort.is_set():
                return
            await _send_binary(ws, device_id, 0x02, chunk)

    queue = SegmentQueue(synthesize=synth_segment, abort_event=abort)

    # accumulated stores the full reply for resolve_follow_up.
    accumulated = ""
    response_done: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    def on_segment_flush(text: str) -> None:
        cleaned = strip_follow_up_tag_inline(text)
        if cleaned:
            queue.push(cleaned)

    def on_segment_end(final_segment: str | None) -> None:
        if final_segment is not None:
            cleaned = strip_follow_up_tag_inline(final_segment)
            if cleaned:
                queue.push(cleaned)
        queue.close()

    segmenter = IdleSegmenter(
        idle_pause_ms=idle_pause_ms, on_segment=on_segment_flush, on_end=on_segment_end
    )

    # Channel listener — these are called from the channel server's reader.
    def on_delta(_run_id: str, text: str) -> None:
        nonlocal accumulated
        if response_done.done():
            return
        accumulated += text
        segmenter.push(text)

    def on_end(_run_id: str) -> None:
        if response_done.done():
            return
        segmenter.end()
        response_done.set_result(accumulated)

    def on_error(_run_id: str, message: str) -> None:
        if response_done.done():
            return
        segmenter.abort()
        queue.close()
        response_done.set_exception(RuntimeError(message))

    channel_server.add_response_listener(
        device_id, {"on_delta": on_delta, "on_end": on_end, "on_error": on_error}
    )

    # If abort fires, finish.
    abort_waiter = asyncio.create_task(abort.wait())

    def _on_abort(_t: asyncio.Task) -> None:
        if not response_done.done():
            segmenter.abort()
            queue.close()
            response_done.set_exception(RuntimeError("Aborted"))

    abort_waiter.add_done_callback(_on_abort)

    try:
        full_reply = await asyncio.wait_for(response_done, timeout=_CHANNEL_RESPONSE_TIMEOUT_S)
    except asyncio.TimeoutError:
        segmenter.abort()
        queue.close()
        await queue.done()
        abort_waiter.cancel()
        channel_server.remove_response_listener(device_id)
        if abort.is_set():
            return
        await _send_json(
            ws,
            {
                "type": "error",
                "code": "BACKEND_ERROR",
                "message": f"Channel response timeout after {int(_CHANNEL_RESPONSE_TIMEOUT_S)}s",
            },
        )
        await _synthesize_error_message(ws, device_id, abort, target_rate)
        if not abort.is_set():
            await _send_audio_end(ws, False)
        return
    except RuntimeError as err:
        await queue.done()
        abort_waiter.cancel()
        channel_server.remove_response_listener(device_id)
        if abort.is_set():
            return
        await _send_json(
            ws, {"type": "error", "code": "BACKEND_ERROR", "message": str(err)}
        )
        await _synthesize_error_message(ws, device_id, abort, target_rate)
        if not abort.is_set():
            await _send_audio_end(ws, False)
        return

    abort_waiter.cancel()
    channel_server.remove_response_listener(device_id)
    await queue.done()

    if abort.is_set():
        return

    result = resolve_follow_up(full_reply, _follow_up_mode_for(device_id))
    log.info(
        "Channel reply (%d chars, follow_up=%s): %s",
        len(result.reply_text),
        result.follow_up,
        result.reply_text[:200],
    )
    await _send_audio_end(ws, result.follow_up)


async def run_voice_turn(
    device_id: str,
    audio_chunks: list[bytes],
    ws: Any,
    openclaw_client: "OpenClawClient | None",
    channel_server: "ChannelServer",
    abort: asyncio.Event,
    target_rate: int | None = None,
) -> None:
    """Drive a full voice turn: STT, route to active channel, TTS, audio.end."""

    if abort.is_set():
        return

    try:
        transcript_text = await transcribe(audio_chunks)
    except Exception as err:  # noqa: BLE001
        log.error("STT error for %s: %s", device_id, err)
        await _send_json(ws, {"type": "error", "code": "STT_ERROR", "message": str(err)})
        return

    if abort.is_set():
        return

    if not transcript_text or not transcript_text.strip():
        log.info("Empty transcript for %s — ending turn", device_id)
        await _send_audio_end(ws, False)
        return

    log.info("Transcript for %s: %r", device_id, transcript_text)
    await _send_json(ws, {"type": "transcript", "text": transcript_text})

    if abort.is_set():
        return

    active = channel_server.get_active_channel()
    if active is not None and getattr(active, "type", None) == "openclaw-direct" and openclaw_client is not None:
        log.info("Routing via openclaw-direct for %s", device_id)
        await _route_via_openclaw_direct(
            device_id, transcript_text, ws, openclaw_client, abort, target_rate
        )
    elif active is not None and getattr(active, "type", None) != "openclaw-direct":
        log.info("Routing via channel %r for %s", getattr(active, "name", "?"), device_id)
        await _route_via_channel(device_id, transcript_text, ws, channel_server, abort, target_rate)
    else:
        log.warning("No active channel or backend available — dropping turn")
        await _send_json(
            ws, {"type": "error", "code": "NO_CHANNEL", "message": "No active channel configured"}
        )
        await _send_audio_end(ws, False)
