"""Wyoming TTS client (Piper).

Port of `src/wyoming-tts.ts`. Streams synthesized PCM back as it arrives,
optionally resampling to a device-specific output rate via cascaded biquad
LPF + linear interpolation.
"""

from __future__ import annotations

import asyncio
import logging
import math
import struct
from collections.abc import AsyncIterator, Callable
from typing import Any

from .config import get_config
from .wyoming_stt import WyomingEvent, encode_event, parse_wyoming_events

log = logging.getLogger("vauxr.wyoming_tts")


def _make_biquad_lpf(cutoff_hz: float, sample_rate: float, q: float) -> Callable[[float], float]:
    w0 = 2 * math.pi * cutoff_hz / sample_rate
    sin_w0 = math.sin(w0)
    cos_w0 = math.cos(w0)
    alpha = sin_w0 / (2 * q)
    a0 = 1 + alpha
    b0 = ((1 - cos_w0) / 2) / a0
    b1 = (1 - cos_w0) / a0
    b2 = b0
    a1 = (-2 * cos_w0) / a0
    a2 = (1 - alpha) / a0

    state = {"x1": 0.0, "x2": 0.0, "y1": 0.0, "y2": 0.0}

    def step(x0_: float) -> float:
        y0 = b0 * x0_ + b1 * state["x1"] + b2 * state["x2"] - a1 * state["y1"] - a2 * state["y2"]
        state["x2"] = state["x1"]
        state["x1"] = x0_
        state["y2"] = state["y1"]
        state["y1"] = y0
        return y0

    return step


def _make_resampler(from_rate: int, to_rate: int) -> Callable[[bytes], bytes]:
    """Stateful resampler: 4th-order Butterworth LPF + linear interpolation.

    Cutoff must be below BOTH rates' Nyquist limits (see wyoming-tts.ts
    comment in `createResampler` for the bug this avoids).
    """
    cutoff = min(from_rate, to_rate) / 2 * 0.9
    lpf1 = _make_biquad_lpf(cutoff, from_rate, 0.54119610)
    lpf2 = _make_biquad_lpf(cutoff, from_rate, 1.3065630)

    def resample(buf: bytes) -> bytes:
        src_samples = len(buf) // 2
        if src_samples == 0:
            return b""

        # Apply cascaded biquads in float.
        samples = struct.unpack_from(f"<{src_samples}h", buf)
        filtered = [lpf2(lpf1(s)) for s in samples]

        dst_samples = round(src_samples * to_rate / from_rate)
        out = bytearray(dst_samples * 2)
        for i in range(dst_samples):
            pos = i * from_rate / to_rate
            idx = int(pos)
            frac = pos - idx
            s0 = filtered[idx] if idx < src_samples else 0.0
            s1 = filtered[idx + 1] if idx + 1 < src_samples else s0
            val = round(s0 * (1 - frac) + s1 * frac)
            if val > 32767:
                val = 32767
            elif val < -32768:
                val = -32768
            struct.pack_into("<h", out, i * 2, val)
        return bytes(out)

    return resample


class _Aborted(Exception):
    """Internal signal used to bail out of synthesize() on abort."""


async def synthesize(
    text: str,
    *,
    target_rate: int | None = None,
    abort_event: asyncio.Event | None = None,
    on_sample_rate: Callable[[int], None] | None = None,
) -> AsyncIterator[bytes]:
    """Stream synthesized PCM from Piper.

    `abort_event` mirrors the AbortSignal used by the Node port — the
    generator stops mid-stream when it fires.
    """
    cfg = get_config()
    host, port = cfg.piper.host, cfg.piper.port

    reader, writer = await asyncio.open_connection(host, port)

    try:
        writer.write(
            encode_event(
                WyomingEvent(type="synthesize", data={"text": text, "voice": {"name": cfg.piper.voice}})
            )
        )
        await writer.drain()

        buf = b""
        piper_rate = 0
        resample: Callable[[bytes], bytes] | None = None
        sample_rate_fired = False

        while True:
            if abort_event is not None and abort_event.is_set():
                raise _Aborted()

            data = await reader.read(8192)
            if not data:
                break
            buf += data
            events, buf = parse_wyoming_events(buf)

            stop = False
            for ev in events:
                if ev.type == "audio-start" and isinstance(ev.data.get("rate"), (int, float)):
                    piper_rate = int(ev.data["rate"])
                elif ev.type == "audio-chunk":
                    if piper_rate == 0 and isinstance(ev.data.get("rate"), (int, float)):
                        piper_rate = int(ev.data["rate"])
                    if ev.payload:
                        # Fire on_sample_rate once, on first chunk we can ship.
                        if not sample_rate_fired and on_sample_rate is not None and piper_rate:
                            effective = target_rate if (target_rate and target_rate != piper_rate) else piper_rate
                            on_sample_rate(effective)
                            sample_rate_fired = True
                        if target_rate and piper_rate and piper_rate != target_rate:
                            if resample is None:
                                resample = _make_resampler(piper_rate, target_rate)
                            yield resample(ev.payload)
                        else:
                            yield ev.payload
                elif ev.type == "audio-stop":
                    stop = True

            if stop:
                break
    except _Aborted:
        return
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass
