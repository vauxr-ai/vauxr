"""Phase 3: protocol frame parsing."""

from __future__ import annotations

import pytest

import protocol


def test_parse_binary_frame_basic() -> None:
    frame = protocol.parse_binary_frame(b"\x01\x00\x05hello")
    assert frame is not None
    assert frame.msg_type == 0x01
    assert frame.seq == 5
    assert frame.payload == b"hello"


def test_parse_binary_frame_empty_payload() -> None:
    frame = protocol.parse_binary_frame(b"\x02\xff\xff")
    assert frame is not None
    assert frame.msg_type == 0x02
    assert frame.seq == 0xFFFF
    assert frame.payload == b""


def test_parse_binary_frame_short_drops() -> None:
    assert protocol.parse_binary_frame(b"") is None
    assert protocol.parse_binary_frame(b"\x01") is None
    assert protocol.parse_binary_frame(b"\x01\x00") is None


def test_make_binary_frame_round_trip() -> None:
    for msg_type in (1, 2, 3):
        for seq in (0, 1, 256, 0xFFFF):
            payload = bytes(range(64))
            frame = protocol.make_binary_frame(msg_type, seq, payload)
            parsed = protocol.parse_binary_frame(frame)
            assert parsed is not None
            assert parsed.msg_type == msg_type
            assert parsed.seq == seq
            assert parsed.payload == payload


def test_is_text_frame_brace() -> None:
    assert protocol.is_text_frame(b'{"type":"voice.start"}')
    assert protocol.is_text_frame(b'   \n  {"type":"voice.end"}')


def test_is_text_frame_binary_first_byte() -> None:
    assert protocol.is_text_frame(b"\x01\x00\x00") is False
    assert protocol.is_text_frame(b"") is False


def test_parse_text_message_valid() -> None:
    msg = protocol.parse_text_message('{"type":"voice.start","device_id":"d1","token":"t"}')
    assert msg == {"type": "voice.start", "device_id": "d1", "token": "t"}


def test_parse_text_message_bytes() -> None:
    msg = protocol.parse_text_message(b'{"type":"ready"}')
    assert msg == {"type": "ready"}


def test_parse_text_message_invalid_json() -> None:
    assert protocol.parse_text_message("{not json") is None
    assert protocol.parse_text_message("") is None


def test_parse_text_message_non_object() -> None:
    assert protocol.parse_text_message("[]") is None
    assert protocol.parse_text_message('"plain string"') is None
    assert protocol.parse_text_message("42") is None


def test_parse_text_message_invalid_utf8() -> None:
    assert protocol.parse_text_message(b"\xff\xfe\xfd") is None


def test_encode_text_message_compact() -> None:
    # Match the Node JSON.stringify output: no whitespace between tokens.
    assert protocol.encode_text_message({"type": "ready"}) == '{"type":"ready"}'
    assert (
        protocol.encode_text_message({"type": "audio.end", "follow_up": False})
        == '{"type":"audio.end","follow_up":false}'
    )


def test_frame_type_enum_values() -> None:
    assert protocol.FrameType.MIC_AUDIO == 0x01
    assert protocol.FrameType.TTS_AUDIO == 0x02
    assert protocol.FrameType.PUSH_AUDIO == 0x03


@pytest.mark.parametrize(
    "data,expected_type",
    [
        (b"\x01\x00\x00", 0x01),
        (b"\x02\x00\x00", 0x02),
        (b"\x03\x00\x00", 0x03),
        (b"\x99\x00\x00", 0x99),  # unknown type still parses; server filters
    ],
)
def test_parse_binary_frame_types(data: bytes, expected_type: int) -> None:
    frame = protocol.parse_binary_frame(data)
    assert frame is not None
    assert frame.msg_type == expected_type
