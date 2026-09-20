"""Output gain for the GPT-Live pipeline.

GPT-Live's spoken PCM lands noticeably quieter than the Standard-mode TTS
backends at the same device volume. Devices apply one hardware volume to both
paths, so the level is equalised here: 16-bit PCM leaving the Live service is
scaled by a configurable gain before it reaches the WebRTC output track. A
soft knee above ``_KNEE`` keeps loud peaks from hard-clipping once boosted.
"""
from __future__ import annotations

import math
from array import array

_FULL_SCALE = 32767.0
# Samples above this fraction of full scale are compressed instead of clipped.
_KNEE = 0.85


def db_to_linear(gain_db: float) -> float:
    """Convert a dB gain to a linear multiplier."""
    return 10.0 ** (gain_db / 20.0)


def _soft_clip(sample: float) -> float:
    magnitude = abs(sample)
    if magnitude <= _KNEE:
        return sample
    headroom = 1.0 - _KNEE
    compressed = _KNEE + headroom * math.tanh((magnitude - _KNEE) / headroom)
    return math.copysign(compressed, sample)


def apply_gain(pcm: bytes, gain: float) -> bytes:
    """Scale little-endian 16-bit mono/interleaved PCM by ``gain`` with soft clipping.

    ``gain`` is linear (1.0 = unchanged). Odd trailing bytes are preserved untouched.
    """
    if gain == 1.0 or not pcm:
        return pcm
    usable = len(pcm) - (len(pcm) % 2)
    samples = array("h")
    samples.frombytes(pcm[:usable])
    for i, sample in enumerate(samples):
        scaled = _soft_clip(sample * gain / _FULL_SCALE) * _FULL_SCALE
        samples[i] = int(max(-32768.0, min(32767.0, scaled)))
    return samples.tobytes() + pcm[usable:]
