"""In-memory registry of connected devices + per-device persisted config.

Port of `src/device-registry.ts`. The on-disk schema is owned by
`device_config.py`; this module keeps the live connection state and routes
config updates through to disk.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from config import get_config
from device_config import DeviceConfig, load_device_configs, save_device_configs

ConnectionState = Literal["idle", "listening", "processing", "speaking", "offline"]

# Native playback rates for known hardware. Announce/TTS used to learn the
# sink rate from voice.start / realtime.start; hello now registers idle
# devices so we must know it before the first turn. Firmware may also send
# `output_sample_rate` on hello — that wins over this table.
PLATFORM_OUTPUT_RATES: dict[str, int] = {
    "satellite1": 48000,
    "waveshare": 16000,
}


@dataclass
class DeviceEntry:
    id: str
    name: str
    ws: Any  # opaque — aiohttp.web.WebSocketResponse in production
    state: ConnectionState = "idle"
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    seq: int = 0
    abort_event: asyncio.Event | None = None
    config: DeviceConfig = field(default_factory=dict)
    output_sample_rate: int | None = None
    platform: str | None = None
    fw_version: str | None = None


_devices: dict[str, DeviceEntry] = {}
_configs: dict[str, DeviceConfig] = {}
_configs_loaded = False


def _ensure_configs_loaded() -> None:
    global _configs, _configs_loaded
    if _configs_loaded:
        return
    _configs = load_device_configs(get_config().data_dir)
    _configs_loaded = True


def load_configs() -> None:
    global _configs, _configs_loaded
    _configs = load_device_configs(get_config().data_dir)
    _configs_loaded = True


def get_config_for(device_id: str) -> DeviceConfig:
    _ensure_configs_loaded()
    return _configs.get(device_id, {})


def update_config(device_id: str, patch: DeviceConfig) -> DeviceConfig:
    _ensure_configs_loaded()
    merged: DeviceConfig = {**_configs.get(device_id, {}), **patch}
    _configs[device_id] = merged
    save_device_configs(get_config().data_dir, _configs)
    entry = _devices.get(device_id)
    if entry is not None:
        entry.config = merged
    return merged


def register(device_id: str, ws: Any, name: str | None = None) -> DeviceEntry:
    abort_active_turn(device_id)
    _ensure_configs_loaded()
    cfg = _configs.get(device_id, {})
    prev = _devices.get(device_id)
    entry = DeviceEntry(
        id=device_id,
        name=name or cfg.get("name") or (prev.name if prev else None) or device_id,
        ws=ws,
        config=cfg,
    )
    if prev is not None:
        entry.platform = prev.platform
        entry.fw_version = prev.fw_version
        entry.output_sample_rate = prev.output_sample_rate
    _devices[device_id] = entry
    return entry


def unregister(device_id: str, ws: Any | None = None) -> None:
    """Drop a live session.

    If ``ws`` is given, only unregister when that socket still owns the
    device. A reconnect (OTA reboot, flaky Wi-Fi) registers the new
    connection first; the old handler's ``finally`` must not wipe it.
    """
    entry = _devices.get(device_id)
    if entry is None:
        return
    if ws is not None and entry.ws is not ws:
        return
    abort_active_turn(device_id)
    _devices.pop(device_id, None)


def get(device_id: str) -> DeviceEntry | None:
    return _devices.get(device_id)


def set_hello_info(
    device_id: str, platform: str | None = None, fw_version: str | None = None
) -> None:
    """Stamp identity advertised in the boot-time hello frame."""
    entry = _devices.get(device_id)
    if entry is None:
        return
    if platform:
        entry.platform = platform
    if fw_version:
        entry.fw_version = fw_version


def resolve_output_sample_rate(
    msg: dict[str, Any],
    *,
    config: DeviceConfig | None = None,
    platform: str | None = None,
) -> int | None:
    """Playback rate from hello/voice.start, then persisted config, then platform."""
    raw = msg.get("output_sample_rate") or msg.get("sample_rate")
    if isinstance(raw, (int, float)) and raw > 0:
        return int(raw)
    if config:
        cfg_rate = config.get("output_sample_rate")
        if isinstance(cfg_rate, (int, float)) and cfg_rate > 0:
            return int(cfg_rate)
    if platform and platform in PLATFORM_OUTPUT_RATES:
        return PLATFORM_OUTPUT_RATES[platform]
    return None


def apply_output_sample_rate(
    device_id: str,
    msg: dict[str, Any],
    *,
    platform: str | None = None,
) -> int | None:
    """Stamp ``DeviceEntry.output_sample_rate`` from a hello / start message."""
    entry = _devices.get(device_id)
    if entry is None:
        return None
    plat = platform if platform is not None else entry.platform
    rate = resolve_output_sample_rate(msg, config=entry.config, platform=plat)
    if rate is not None:
        entry.output_sample_rate = rate
    return rate


def get_all() -> list[DeviceEntry]:
    return list(_devices.values())


def set_state(device_id: str, state: ConnectionState) -> None:
    entry = _devices.get(device_id)
    if entry is None:
        return
    entry.state = state
    entry.last_seen = datetime.now(timezone.utc)


def abort_active_turn(device_id: str) -> None:
    entry = _devices.get(device_id)
    if entry is None or entry.abort_event is None:
        return
    entry.abort_event.set()
    entry.abort_event = None


def next_seq(device_id: str) -> int:
    entry = _devices.get(device_id)
    if entry is None:
        return 0
    entry.seq = (entry.seq + 1) & 0xFFFF
    return entry.seq


def reset() -> None:
    """Clear all in-memory state. Test helper only."""
    global _configs, _configs_loaded
    _devices.clear()
    _configs = {}
    _configs_loaded = False
