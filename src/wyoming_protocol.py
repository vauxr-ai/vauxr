"""Shared Wyoming event framing and provider-neutral protocol errors."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class WyomingError(RuntimeError):
    """The configured speech service rejected or failed a protocol request."""


@dataclass
class WyomingEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    payload: bytes | None = None


def encode_event(event: WyomingEvent) -> bytes:
    obj: dict[str, Any] = {"type": event.type, "data": event.data}
    if event.payload and len(event.payload) > 0:
        obj["payload_length"] = len(event.payload)
    line = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
    if event.payload and len(event.payload) > 0:
        return line + event.payload
    return line


def parse_wyoming_events(buf: bytes) -> tuple[list[WyomingEvent], bytes]:
    """Streaming parser. Returns parsed events + leftover bytes."""
    events: list[WyomingEvent] = []
    offset = 0
    n = len(buf)

    while offset < n:
        nl = buf.find(b"\n", offset)
        if nl == -1:
            break

        line_start = offset
        line = buf[offset:nl]
        offset = nl + 1

        try:
            parsed = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Malformed header — skip to next line, matching Node behavior.
            continue

        if not isinstance(parsed, dict):
            continue

        data_len = int(parsed.get("data_length") or 0)
        payload_len = int(parsed.get("payload_length") or 0)
        total_trailing = data_len + payload_len

        if total_trailing > 0 and offset + total_trailing > n:
            # Need more bytes — rewind to the header start.
            offset = line_start
            break

        data: dict[str, Any] = parsed.get("data") or {}
        if data_len > 0:
            try:
                data = json.loads(buf[offset : offset + data_len].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # keep the header's `data` — same fallback as Node
                pass
            offset += data_len

        payload: bytes | None = None
        if payload_len > 0:
            payload = bytes(buf[offset : offset + payload_len])
            offset += payload_len

        events.append(WyomingEvent(type=parsed.get("type", ""), data=data, payload=payload))

    return events, bytes(buf[offset:])
