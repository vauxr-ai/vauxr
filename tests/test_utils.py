"""Phase 2: utils.make_binary_frame."""

from __future__ import annotations

import pytest

from vauxr.utils import make_binary_frame


def test_basic_frame() -> None:
    frame = make_binary_frame(0x02, 1, b"hello")
    assert frame == b"\x02\x00\x01hello"


def test_zero_seq() -> None:
    assert make_binary_frame(0x01, 0, b"") == b"\x01\x00\x00"


def test_max_seq_wraps_big_endian() -> None:
    # seq is big-endian uint16
    frame = make_binary_frame(0x03, 0xFFFF, b"")
    assert frame == b"\x03\xff\xff"


def test_msg_type_out_of_range() -> None:
    with pytest.raises(ValueError):
        make_binary_frame(0x100, 0, b"")
    with pytest.raises(ValueError):
        make_binary_frame(-1, 0, b"")


def test_seq_out_of_range() -> None:
    with pytest.raises(ValueError):
        make_binary_frame(0x01, 0x10000, b"")
    with pytest.raises(ValueError):
        make_binary_frame(0x01, -1, b"")
