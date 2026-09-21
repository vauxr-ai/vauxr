"""Output gain applied to GPT-Live speech so it matches Standard-mode loudness."""
from array import array

import pytest

from vauxr.realtime.gain import apply_gain, db_to_linear


def _pcm(*samples: int) -> bytes:
    return array("h", samples).tobytes()


def _samples(pcm: bytes) -> list[int]:
    out = array("h")
    out.frombytes(pcm)
    return list(out)


def test_db_to_linear_matches_six_db_doubling():
    assert db_to_linear(0) == 1.0
    assert db_to_linear(6.0) == pytest.approx(1.995, abs=0.001)


def test_unity_gain_and_empty_input_are_passthrough():
    pcm = _pcm(100, -200, 300)
    assert apply_gain(pcm, 1.0) is pcm
    assert apply_gain(b"", 2.0) == b""


def test_quiet_audio_scales_linearly_and_keeps_sign():
    assert _samples(apply_gain(_pcm(1000, -1000, 0), 2.0)) == [2000, -2000, 0]


def test_loud_peaks_are_soft_clipped_not_wrapped():
    boosted = _samples(apply_gain(_pcm(30000, -30000, 32767), 2.0))
    assert all(abs(s) <= 32767 for s in boosted)
    # Above the knee the curve compresses: louder than the knee, below hard clip.
    assert 0.85 * 32767 < boosted[0] < 32767
    assert boosted[1] == -boosted[0]
    # Monotonic: a louder input never comes out quieter.
    assert boosted[2] >= boosted[0]


def test_odd_trailing_byte_is_preserved():
    pcm = _pcm(1000) + b"\x7f"
    out = apply_gain(pcm, 2.0)
    assert out[-1:] == b"\x7f"
    assert _samples(out[:-1]) == [2000]


def test_realtime_config_reads_output_gain(monkeypatch):
    import vauxr.config as config

    monkeypatch.setenv("REALTIME_OUTPUT_GAIN_DB", "3.5")
    assert config.load_config().realtime.output_gain_db == 3.5
    monkeypatch.delenv("REALTIME_OUTPUT_GAIN_DB")
    assert config.load_config().realtime.output_gain_db == 6.0


async def test_output_gain_processor_scales_live_speech_frames(monkeypatch):
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import SpeechOutputAudioRawFrame, TextFrame
    from pipecat.processors.frame_processor import FrameDirection

    from vauxr.realtime.live import OutputGain

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    gain = OutputGain(6.0)
    seen: list[object] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        seen.append(frame)

    gain.push_frame = capture  # type: ignore[method-assign]
    audio = SpeechOutputAudioRawFrame(audio=_pcm(1000, -1000), sample_rate=24000, num_channels=1)
    text = TextFrame(text="unchanged")
    await gain.process_frame(audio, FrameDirection.DOWNSTREAM)
    await gain.process_frame(text, FrameDirection.DOWNSTREAM)
    assert seen == [audio, text]
    assert _samples(audio.audio) == [1995, -1995]
