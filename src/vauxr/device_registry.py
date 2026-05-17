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

from .config import get_config
from .device_config import DeviceConfig, load_device_configs, save_device_configs

ConnectionState = Literal["idle", "listening", "processing", "speaking", "offline"]


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
    entry = DeviceEntry(
        id=device_id,
        name=name or cfg.get("name") or device_id,
        ws=ws,
        config=cfg,
    )
    _devices[device_id] = entry
    return entry


def unregister(device_id: str) -> None:
    abort_active_turn(device_id)
    _devices.pop(device_id, None)


def get(device_id: str) -> DeviceEntry | None:
    return _devices.get(device_id)


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
