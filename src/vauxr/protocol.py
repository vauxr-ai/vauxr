"""Pure-function Vauxr WS protocol parsing.

The Vauxr protocol multiplexes two kinds of frame on one WebSocket:

* JSON control messages (text frames) — `{ "type": ..., ... }`
* Audio frames (binary frames) with a 3-byte header:
    [1 byte: message type][2 bytes: sequence (big-endian)][raw payload]

Message-type bytes:
  0x01  device → server   mic audio (raw PCM 16-bit, 16 kHz, mono)
  0x02  server → device   TTS audio
  0x03  server → device   proactive push audio

This module is I/O-free: it parses bytes/strings into typed records and
builds frames back out. Used by the WS server and tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class FrameType(IntEnum):
    MIC_AUDIO = 0x01
    TTS_AUDIO = 0x02
    PUSH_AUDIO = 0x03


# Optional heuristic for clients without frame-type metadata. The aiohttp server
# dispatches using WSMsgType; it does not sniff microphone bytes for JSON.
JSON_FIRST_BYTE = 0x7B


@dataclass(frozen=True)
class BinaryFrame:
    """A parsed binary audio frame."""

    msg_type: int
    seq: int
    payload: bytes


def is_text_frame(data: bytes) -> bool:
    """Heuristic: a text frame starts with `{` after any leading whitespace.

    Useful for clients that receive raw bytes without an explicit text/binary
    flag. The server itself dispatches using aiohttp's WSMsgType discriminator.
    """
    for b in data:
        if b in (0x09, 0x0A, 0x0D, 0x20):
            continue
        return b == JSON_FIRST_BYTE
    return False


def parse_binary_frame(data: bytes) -> BinaryFrame | None:
    """Parse a binary audio frame. Returns None if too short.

    Requires the complete 3-byte header; payload may be empty. This parser
    does not validate audio type, ordering or PCM shape; consumers own those checks.
    """
    if len(data) < 3:
        return None
    msg_type = data[0]
    seq = int.from_bytes(data[1:3], "big")
    payload = bytes(data[3:])
    return BinaryFrame(msg_type=msg_type, seq=seq, payload=payload)


def make_binary_frame(msg_type: int, seq: int, payload: bytes) -> bytes:
    """Build a binary audio frame — re-exported from utils for cohesion."""
    from vauxr.utils import make_binary_frame as _impl

    return _impl(msg_type, seq, payload)


# --- Control message parsing ---

# Parsing is deliberately separate from authorization and operation validation.
# A JSON object here is not an admitted control message; the dispatcher applies
# credential, identity and operation checks before invoking a handler.


def parse_text_message(data: str | bytes) -> dict[str, Any] | None:
    """Parse a JSON text frame; return None on invalid JSON.

    The result is always a `dict` for control messages — we reject arrays or
    primitives that some clients might send by accident.
    """
    if isinstance(data, (bytes, bytearray)):
        try:
            data = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def encode_text_message(obj: dict[str, Any]) -> str:
    """Encode a control message as compact JSON."""
    return json.dumps(obj, separators=(",", ":"))
