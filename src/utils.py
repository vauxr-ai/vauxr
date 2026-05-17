"""Small helpers shared across the package."""

from __future__ import annotations


def make_binary_frame(msg_type: int, seq: int, payload: bytes) -> bytes:
    """Build a Vauxr binary WS frame.

    Layout (matches `src/utils.ts` and the protocol spec):
      [1 byte: message type][2 bytes: sequence (big-endian)][raw payload]
    """
    if not 0 <= msg_type <= 0xFF:
        raise ValueError(f"msg_type out of range: {msg_type}")
    if not 0 <= seq <= 0xFFFF:
        raise ValueError(f"seq out of range: {seq}")
    return bytes((msg_type,)) + seq.to_bytes(2, "big") + payload
