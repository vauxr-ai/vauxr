"""Dispatch action-button gestures using per-device mappings."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

import device_registry as registry
import webhooks
from device_config import VALID_GESTURES, ButtonAction
from pipeline import run_text_turn
from protocol import encode_text_message
from utils import make_binary_frame
from wyoming_tts import synthesize

if TYPE_CHECKING:
    from channel_server import ChannelServer
    from openclaw_client import OpenClawClient

log = logging.getLogger("vauxr.button")


async def handle_device_button(
    *,
    device_id: str,
    button: str,
    gesture: str,
    openclaw_client: OpenClawClient | None,
    channel_server: ChannelServer,
) -> None:
    if gesture not in VALID_GESTURES:
        log.warning("device.button unknown gesture %r from %s", gesture, device_id)
        return

    cfg = registry.get_config_for(device_id)
    actions = cfg.get("button_actions") or {}
    action: ButtonAction | None = actions.get(gesture) if isinstance(actions, dict) else None
    kind = (action or {}).get("kind") or "none"
    log.info(
        "device.button %s button=%s gesture=%s kind=%s",
        device_id,
        button,
        gesture,
        kind,
    )
    if not action or kind == "none":
        return

    if kind == "webhook":
        await _dispatch_webhook(device_id, button, gesture, action)
    elif kind == "announce":
        await _dispatch_announce(device_id, action.get("text") or "")
    elif kind == "command":
        await _dispatch_command(device_id, action)
    elif kind == "prompt":
        await _dispatch_prompt(
            device_id,
            action.get("text") or "",
            openclaw_client,
            channel_server,
        )
    else:
        log.warning("device.button unhandled kind %r for %s", kind, device_id)


async def _dispatch_webhook(device_id: str, button: str, gesture: str, action: ButtonAction) -> None:
    webhook_id = action.get("webhook_id") or ""
    hook = webhooks.get(webhook_id)
    if hook is None:
        log.warning("device.button webhook %r not found (device %s)", webhook_id, device_id)
        return
    payload = {
        "type": "vauxr.button_press",
        "device_id": device_id,
        "button": button,
        "gesture": gesture,
    }
    headers = {"Content-Type": "application/json"}
    if hook.authorization:
        headers["Authorization"] = hook.authorization
    timeout = aiohttp.ClientTimeout(total=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(hook.url, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    log.warning(
                        "webhook %s (%s) returned %s for %s %s",
                        hook.name,
                        hook.id,
                        resp.status,
                        device_id,
                        gesture,
                    )
                else:
                    log.info(
                        "webhook %s fired for %s %s → %s",
                        hook.name,
                        device_id,
                        gesture,
                        resp.status,
                    )
    except Exception as err:  # noqa: BLE001
        log.warning("webhook %s (%s) failed: %s", hook.name, hook.id, err)


async def _dispatch_announce(device_id: str, text: str) -> None:
    text = text.strip()
    if not text:
        return
    entry = registry.get(device_id)
    if entry is None:
        return
    if entry.state in ("listening", "processing"):
        log.info("device.button announce dropped, device busy: %s", device_id)
        return
    await announce_to_device(entry, text)


async def announce_to_device(device: Any, text: str) -> None:
    """Synthesize ``text`` and stream it as 0x03 announce frames."""
    abort = asyncio.Event()
    sent_start = {"v": False}

    def on_rate(rate: int) -> None:
        if not sent_start["v"]:
            asyncio.create_task(
                _send_text(device.ws, {"type": "audio.start", "sample_rate": rate})
            )
            sent_start["v"] = True

    chunk_count = 0
    try:
        async for chunk in synthesize(
            text, target_rate=device.output_sample_rate, abort_event=abort, on_sample_rate=on_rate
        ):
            seq = registry.next_seq(device.id)
            await _send_bytes(device.ws, make_binary_frame(0x03, seq, chunk))
            chunk_count += 1
    except Exception as err:  # noqa: BLE001
        log.error("TTS error for announce to %s: %s", device.id, err)

    await _send_text(device.ws, {"type": "audio.end"})
    log.info("announce: done %s, %d chunks sent", device.id, chunk_count)


async def _dispatch_command(device_id: str, action: ButtonAction) -> None:
    entry = registry.get(device_id)
    if entry is None:
        return
    command = action.get("command")
    if not command:
        return
    frame: dict[str, Any] = {"type": "device.control", "command": command}
    if command == "set_volume":
        frame["params"] = {"volume": action.get("volume", 0)}
    await _send_text(entry.ws, frame)
    log.info("device.button command %s → %s", command, device_id)


async def _dispatch_prompt(
    device_id: str,
    text: str,
    openclaw_client: OpenClawClient | None,
    channel_server: ChannelServer,
) -> None:
    text = text.strip()
    if not text:
        return
    entry = registry.get(device_id)
    if entry is None:
        return
    if entry.state in ("listening", "processing"):
        log.info("device.button prompt dropped, device busy: %s", device_id)
        return

    from realtime_session import get_manager

    manager = get_manager()
    # Live Pipecat session (including idle/warm-quiet): seed so TTS rides the
    # WebRTC track and the conversation log stays on the same LLMContext.
    # Classic WS devices have no session — run_text_turn over 0x02.
    if manager.has_live_session(device_id):
        await manager.seed_text_turn(device_id, text)
        return

    abort = asyncio.Event()
    entry.abort_event = abort
    registry.set_state(device_id, "processing")
    try:
        await run_text_turn(
            device_id,
            text,
            entry.ws,
            openclaw_client,
            channel_server,
            abort,
            entry.output_sample_rate,
        )
    except Exception as err:  # noqa: BLE001
        log.error("device.button prompt failed for %s: %s", device_id, err)
    finally:
        e = registry.get(device_id)
        if e is not None:
            e.abort_event = None
            registry.set_state(device_id, "idle")


async def _send_text(ws: Any, obj: dict[str, Any]) -> None:
    if getattr(ws, "closed", False):
        return
    try:
        await ws.send_str(encode_text_message(obj))
    except ConnectionResetError:
        pass


async def _send_bytes(ws: Any, data: bytes) -> None:
    if getattr(ws, "closed", False):
        return
    try:
        await ws.send_bytes(data)
    except ConnectionResetError:
        pass
