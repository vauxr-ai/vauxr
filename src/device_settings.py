"""Per-device feature settings.

These are runtime feature flags (not persisted device config like name/voice in
``device_config.py``). For now they're hardcoded defaults, but every lookup is
keyed by ``device_id`` so that when the devices API + per-device tokens land, the
only change is the data source here — callers already pass ``device_id`` and
never read globals/env for these flags.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default idle-pause window. Mirrors the WS ``streaming_tts.idle_pause_ms`` default
# so realtime and WS feel the same out of the box.
_DEFAULT_IDLE_PAUSE_MS = 400


@dataclass(frozen=True)
class SegmentationSettings:
    """How a device's streamed reply is cut into TTS segments.

    Two mutually exclusive strategies (they can't combine — a downstream sentence
    aggregator would re-buffer idle's partial flushes away):
    - ``sentence``: pipecat's built-in TTS sentence aggregator (NLTK) cuts on
      sentence boundaries. Takes precedence when both flags are set.
    - ``idle``: flush a segment after the token stream pauses for
      ``idle_pause_ms`` (punctuation-independent, low latency).

    Neither set → speak the whole reply once at end.
    """

    idle: bool
    sentence: bool
    idle_pause_ms: int


_DEFAULT_SEGMENTATION = SegmentationSettings(
    idle=True,
    sentence=False,
    idle_pause_ms=_DEFAULT_IDLE_PAUSE_MS,
)


def get_segmentation(device_id: str) -> SegmentationSettings:
    """Resolve a device's TTS segmentation settings.

    TODO(devices-api): source per-device overrides (and the device's own token)
    from device_config / the devices API. For now every device gets the defaults.
    """
    return _DEFAULT_SEGMENTATION
