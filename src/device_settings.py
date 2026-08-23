"""Per-device feature settings.

These are runtime feature flags (not persisted device config like name/voice in
``device_config.py``). For now they're hardcoded defaults, but every lookup is
keyed by ``device_id`` so that when the devices API + per-device tokens land, the
only change is the data source here — callers already pass ``device_id`` and
never read globals/env for these flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Default idle-pause window. Mirrors the WS ``streaming_tts.idle_pause_ms`` default
# so realtime and WS feel the same out of the box.
_DEFAULT_IDLE_PAUSE_MS = 1000

# Warm-idle taper timers handed to the device in the hello realtime policy.
# T_idle1: Active open-mic with no new turn -> Warm-quiet (pause mic, keep peer).
# T_idle2: Warm-quiet inactivity -> Drop (tear down WebRTC, back to WS wake-wait).
_DEFAULT_T_IDLE1_MS = 8_000
_DEFAULT_T_IDLE2_MS = 180_000

# Server-side Silero VAD defaults (Pipecat realtime path). Shared with the
# device via hello so firmware-side tuning stays aligned where applicable.
_DEFAULT_VAD_CONFIDENCE = 0.6
_DEFAULT_VAD_START_SECS = 0.4
# stop_secs is the silence-after-speech that ends a turn. At 0.6 a normal
# mid-sentence breath/pause ended the turn early, splitting one utterance into
# several transcripts that fired overlapping channel turns (which then cancelled
# each other and dropped Nova's real reply). 1.0 coalesces a single utterance
# into one turn. Onset (start_secs) is unchanged, so barge-in stays responsive.
_DEFAULT_VAD_STOP_SECS = 1.0
# This device's post-AEC speech arrives quietly (peak ~0.15, room noise ~0.05),
# so even a 0.2 floor clipped normal-volume utterances. Keep min_volume just
# above the noise floor and let Silero's neural confidence do the real
# speech-vs-noise gating. (The stricter barge-in profile below still guards
# against echo while the bot is speaking.)
_DEFAULT_VAD_MIN_VOLUME = 0.1

# Barge-in profile: applied only while the bot is speaking. The device's XMOS
# AEC leaves a variable residual echo (peak ~0.2-0.65) that the snappy idle
# profile above would mistake for the user and self-interrupt the reply. To keep
# barge-in working without that false trigger we demand stronger, *sustained*
# evidence during playback: a higher neural confidence, a louder volume floor
# (above the typical echo residual), and a longer onset so intermittent echo
# blips that track the bot's phonemes don't latch. A user genuinely talking over
# the bot clears all three. Restored to the idle profile once the reply drains.
_BARGE_IN_VAD_CONFIDENCE = 0.8
_BARGE_IN_VAD_START_SECS = 0.5
# Same anti-fragmentation reasoning as the idle profile: hold the turn open
# through brief pauses so a barge-in utterance stays a single turn.
_BARGE_IN_VAD_STOP_SECS = 1.0
_BARGE_IN_VAD_MIN_VOLUME = 0.4

# TEMP: residual echo still self-interrupts TTS. Flip back to True when AEC
# is converging (see .cursor/rules/realtime-audio-recording.mdc).
_BARGE_IN_ENABLED = False


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


@dataclass(frozen=True)
class TaperSettings:
    """Device-owned warm-idle taper timers (server is source of truth)."""

    t_idle1_ms: int
    t_idle2_ms: int


@dataclass(frozen=True)
class RealtimeVadSettings:
    """Server-side Silero VAD parameters for the Pipecat realtime path."""

    confidence: float
    start_secs: float
    stop_secs: float
    min_volume: float


@dataclass(frozen=True)
class RealtimeDeviceSettings:
    """Realtime feature bundle for one device."""

    segmentation: SegmentationSettings
    taper: TaperSettings
    vad: RealtimeVadSettings
    # Stricter VAD swapped in while the bot is speaking; see _BARGE_IN_VAD_*.
    vad_barge_in: RealtimeVadSettings
    # When False, new user turns are suppressed for the whole bot-speaking
    # window (not just PROCESSING), so echo cannot cancel TTS.
    barge_in: bool


_DEFAULT_SEGMENTATION = SegmentationSettings(
    idle=True,
    sentence=False,
    idle_pause_ms=_DEFAULT_IDLE_PAUSE_MS,
)

_DEFAULT_TAPER = TaperSettings(
    t_idle1_ms=_DEFAULT_T_IDLE1_MS,
    t_idle2_ms=_DEFAULT_T_IDLE2_MS,
)

_DEFAULT_VAD = RealtimeVadSettings(
    confidence=_DEFAULT_VAD_CONFIDENCE,
    start_secs=_DEFAULT_VAD_START_SECS,
    stop_secs=_DEFAULT_VAD_STOP_SECS,
    min_volume=_DEFAULT_VAD_MIN_VOLUME,
)

_DEFAULT_VAD_BARGE_IN = RealtimeVadSettings(
    confidence=_BARGE_IN_VAD_CONFIDENCE,
    start_secs=_BARGE_IN_VAD_START_SECS,
    stop_secs=_BARGE_IN_VAD_STOP_SECS,
    min_volume=_BARGE_IN_VAD_MIN_VOLUME,
)

_DEFAULT_REALTIME = RealtimeDeviceSettings(
    segmentation=_DEFAULT_SEGMENTATION,
    taper=_DEFAULT_TAPER,
    vad=_DEFAULT_VAD,
    vad_barge_in=_DEFAULT_VAD_BARGE_IN,
    barge_in=_BARGE_IN_ENABLED,
)


def get_segmentation(device_id: str) -> SegmentationSettings:
    """Resolve a device's TTS segmentation settings.

    TODO(devices-api): source per-device overrides (and the device's own token)
    from device_config / the devices API. For now every device gets the defaults.
    """
    return get_realtime_settings(device_id).segmentation


def get_taper(device_id: str) -> TaperSettings:
    """Resolve warm-idle taper timers for a device."""
    return get_realtime_settings(device_id).taper


def get_realtime_vad(device_id: str) -> RealtimeVadSettings:
    """Resolve server-side Silero VAD params for a device (idle profile)."""
    return get_realtime_settings(device_id).vad


def get_realtime_vad_barge_in(device_id: str) -> RealtimeVadSettings:
    """Resolve the stricter VAD profile used while the bot is speaking."""
    return get_realtime_settings(device_id).vad_barge_in


def get_realtime_barge_in(device_id: str) -> bool:
    """Whether the user may interrupt the bot while it is speaking."""
    return get_realtime_settings(device_id).barge_in


def get_realtime_settings(device_id: str) -> RealtimeDeviceSettings:
    """Resolve all per-device realtime settings.

    TODO(devices-api): source per-device overrides from device_config / the
    devices API. For now every device gets the defaults.
    """
    _ = device_id
    return _DEFAULT_REALTIME


def realtime_policy_extras(device_id: str) -> dict[str, Any]:
    """Serialize taper + VAD settings for the hello ``realtime`` policy object."""
    rt = get_realtime_settings(device_id)
    return {
        "taper": {
            "t_idle1_ms": rt.taper.t_idle1_ms,
            "t_idle2_ms": rt.taper.t_idle2_ms,
        },
        "vad": {
            "confidence": rt.vad.confidence,
            "start_secs": rt.vad.start_secs,
            "stop_secs": rt.vad.stop_secs,
            "min_volume": rt.vad.min_volume,
        },
    }
