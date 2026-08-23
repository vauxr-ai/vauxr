"""On-disk per-device config (devices.json).

Port of `src/device-config.ts`. The schema must round-trip with the Node
version unchanged — same field names, same validation rules, same warnings.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal, TypedDict

log = logging.getLogger("vauxr.device_config")

FollowUpMode = Literal["auto", "always", "never"]
VALID_FOLLOW_UP_MODES: frozenset[str] = frozenset({"auto", "always", "never"})
KNOWN_FIELDS: frozenset[str] = frozenset(
    {"name", "voice", "follow_up_mode", "output_sample_rate", "barge_in"}
)


class DeviceConfig(TypedDict, total=False):
    name: str
    voice: bool
    follow_up_mode: FollowUpMode
    output_sample_rate: int
    barge_in: bool


def device_config_path(data_dir: str) -> str:
    return os.path.join(data_dir, "devices.json")


def _sanitize_entry(device_id: str, raw: Any) -> DeviceConfig:
    if not isinstance(raw, dict):
        return {}
    cfg: DeviceConfig = {}
    for key, value in raw.items():
        if key not in KNOWN_FIELDS:
            continue
        if key == "name" and isinstance(value, str):
            cfg["name"] = value
        elif key == "voice" and isinstance(value, bool):
            cfg["voice"] = value
        elif key == "follow_up_mode":
            if isinstance(value, str) and value in VALID_FOLLOW_UP_MODES:
                cfg["follow_up_mode"] = value  # type: ignore[typeddict-item]
            else:
                log.warning(
                    "Invalid follow_up_mode for %s: %s — treating as 'auto'",
                    device_id,
                    value,
                )
                cfg["follow_up_mode"] = "auto"
        elif key == "barge_in":
            if isinstance(value, bool):
                cfg["barge_in"] = value
            else:
                log.warning(
                    "Invalid barge_in for %s: %s — treating as enabled",
                    device_id,
                    value,
                )
                cfg["barge_in"] = True
        elif key == "output_sample_rate":
            # Node accepts any positive number; we also accept floats but
            # narrow to int because rates are integral. Booleans are int
            # subclasses in Python — exclude them explicitly.
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                cfg["output_sample_rate"] = int(value)
    return cfg


def barge_in_enabled(cfg: DeviceConfig | None) -> bool:
    """Whether the device may interrupt the bot while it is speaking.

    Missing key → enabled (the historic default). Invalid values are sanitized
    to True on load.
    """
    if not cfg:
        return True
    return cfg.get("barge_in", True) is True


def load_device_configs(data_dir: str) -> dict[str, DeviceConfig]:
    path = device_config_path(data_dir)
    if not os.path.exists(path):
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            parsed = json.load(f)
    except (OSError, json.JSONDecodeError) as err:
        log.warning("Failed to parse %s: %s", path, err)
        return {}

    if not isinstance(parsed, dict):
        log.warning("%s is not a JSON object — ignoring", path)
        return {}

    return {device_id: _sanitize_entry(device_id, entry) for device_id, entry in parsed.items()}


def save_device_configs(data_dir: str, configs: dict[str, DeviceConfig]) -> None:
    os.makedirs(data_dir, exist_ok=True)
    path = device_config_path(data_dir)
    # 2-space indent matches the Node JSON.stringify(_, null, 2) output so
    # devices.json diffs cleanly when viewed across versions.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2)
