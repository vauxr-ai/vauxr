"""Per-device realtime settings: taper timers + VAD params + hello extras."""

from __future__ import annotations

from device_settings import (
    get_realtime_settings,
    get_realtime_vad,
    get_segmentation,
    get_taper,
    realtime_policy_extras,
)


def test_segmentation_defaults_preserved() -> None:
    seg = get_segmentation("dev-A")
    assert seg.idle is True
    assert seg.sentence is False
    assert seg.idle_pause_ms == 1000


def test_taper_defaults() -> None:
    taper = get_taper("dev-A")
    assert taper.t_idle1_ms > 0
    assert taper.t_idle2_ms > 0


def test_vad_defaults() -> None:
    vad = get_realtime_vad("dev-A")
    assert 0.0 < vad.confidence <= 1.0
    assert vad.start_secs > 0
    assert vad.stop_secs == 2.0
    assert vad.min_volume >= 0


def test_realtime_settings_bundle_is_consistent() -> None:
    rt = get_realtime_settings("dev-A")
    assert rt.segmentation == get_segmentation("dev-A")
    assert rt.taper == get_taper("dev-A")
    assert rt.vad == get_realtime_vad("dev-A")
    assert rt.vad_barge_in.stop_secs == 2.0


def test_realtime_policy_extras_shape() -> None:
    extras = realtime_policy_extras("dev-A")
    assert set(extras) == {"taper", "vad"}
    assert set(extras["taper"]) == {"t_idle1_ms", "t_idle2_ms"}
    assert set(extras["vad"]) == {"confidence", "start_secs", "stop_secs", "min_volume"}
    taper = get_taper("dev-A")
    assert extras["taper"]["t_idle1_ms"] == taper.t_idle1_ms
    assert extras["taper"]["t_idle2_ms"] == taper.t_idle2_ms
    assert extras["vad"]["stop_secs"] == 2.0


def test_settings_keyed_by_device_id() -> None:
    # Same defaults for any id today, but the lookup must accept the key.
    assert get_realtime_settings("anything") == get_realtime_settings("else")
