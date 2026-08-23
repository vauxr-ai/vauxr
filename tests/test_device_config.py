"""Phase 5: device_config — round-trip and sanitization."""

from __future__ import annotations

import json
from pathlib import Path

from device_config import (
    barge_in_enabled,
    device_config_path,
    load_device_configs,
    save_device_configs,
)


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_device_configs(str(tmp_path)) == {}


def test_invalid_json_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text("{not json")
    assert load_device_configs(str(tmp_path)) == {}


def test_non_object_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text("[]")
    assert load_device_configs(str(tmp_path)) == {}


def test_round_trip_preserves_known_fields(tmp_path: Path) -> None:
    inp = {
        "dev-A": {
            "name": "Kitchen",
            "voice": True,
            "follow_up_mode": "always",
            "output_sample_rate": 24000,
            "barge_in": False,
        },
        "dev-B": {"name": "Office"},
    }
    save_device_configs(str(tmp_path), inp)  # type: ignore[arg-type]
    loaded = load_device_configs(str(tmp_path))
    assert loaded == inp


def test_unknown_fields_dropped(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text(
        json.dumps({"dev": {"name": "x", "extra": "ignored", "weird": 5}})
    )
    loaded = load_device_configs(str(tmp_path))
    assert loaded == {"dev": {"name": "x"}}


def test_invalid_follow_up_mode_defaults_to_auto(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text(
        json.dumps({"dev": {"follow_up_mode": "garbage"}})
    )
    loaded = load_device_configs(str(tmp_path))
    assert loaded == {"dev": {"follow_up_mode": "auto"}}


def test_invalid_barge_in_defaults_to_enabled(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text(json.dumps({"dev": {"barge_in": "nope"}}))
    loaded = load_device_configs(str(tmp_path))
    assert loaded == {"dev": {"barge_in": True}}


def test_barge_in_false_preserved(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text(json.dumps({"dev": {"barge_in": False}}))
    loaded = load_device_configs(str(tmp_path))
    assert loaded == {"dev": {"barge_in": False}}


def test_barge_in_enabled_defaults_true() -> None:
    assert barge_in_enabled(None) is True
    assert barge_in_enabled({}) is True
    assert barge_in_enabled({"barge_in": True}) is True
    assert barge_in_enabled({"barge_in": False}) is False


def test_invalid_output_sample_rate_dropped(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text(
        json.dumps(
            {
                "a": {"output_sample_rate": 0},  # zero rejected
                "b": {"output_sample_rate": -10},  # negative rejected
                "c": {"output_sample_rate": "16000"},  # string rejected
                "d": {"output_sample_rate": 16000},  # valid
            }
        )
    )
    loaded = load_device_configs(str(tmp_path))
    assert loaded == {"a": {}, "b": {}, "c": {}, "d": {"output_sample_rate": 16000}}


def test_wrong_types_dropped(tmp_path: Path) -> None:
    (tmp_path / "devices.json").write_text(
        json.dumps(
            {
                "dev": {
                    "name": 123,  # not string
                    "voice": "yes",  # not bool
                }
            }
        )
    )
    loaded = load_device_configs(str(tmp_path))
    assert loaded == {"dev": {}}


def test_save_creates_dir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    save_device_configs(str(nested), {"dev": {"name": "x"}})  # type: ignore[arg-type]
    assert (nested / "devices.json").exists()
    assert load_device_configs(str(nested)) == {"dev": {"name": "x"}}


def test_device_config_path_helper(tmp_path: Path) -> None:
    assert device_config_path(str(tmp_path)) == str(tmp_path / "devices.json")
