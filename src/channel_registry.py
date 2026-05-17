"""Routing-channel registry.

Port of `src/channel-registry.ts`. Channels are stored in `channels.json`
with bcrypt-hashed tokens. The virtual `openclaw-direct` channel exists
when `OPENCLAW_URL` is configured. The active selection persists in
`config.json` so it survives restarts.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal

import bcrypt

from config import get_config

BCRYPT_COST = 10
TOKEN_PREFIX = "vx_ch_"
TOKEN_HEX_LEN = 64


ChannelType = Literal["openclaw", "openclaw-direct"]


@dataclass
class Channel:
    id: str
    name: str
    type: ChannelType
    tokenHash: str
    active: bool
    createdAt: str
    builtin: bool | None = None


@dataclass(frozen=True)
class ChannelPublic:
    id: str
    name: str
    type: ChannelType
    active: bool
    createdAt: str
    builtin: bool | None = None


_channels: list[Channel] = []
_openclaw_direct_active = False
_loaded = False


def _channels_path() -> str:
    return os.path.join(get_config().data_dir, "channels.json")


def _config_path() -> str:
    return os.path.join(get_config().data_dir, "config.json")


def _ensure_data_dir() -> None:
    os.makedirs(get_config().data_dir, exist_ok=True)


def _save_channels() -> None:
    _ensure_data_dir()
    serialized = [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "tokenHash": c.tokenHash,
            "active": c.active,
            "createdAt": c.createdAt,
            **({"builtin": c.builtin} if c.builtin is not None else {}),
        }
        for c in _channels
    ]
    with open(_channels_path(), "w", encoding="utf-8") as f:
        json.dump(serialized, f, indent=2)


def _save_config() -> None:
    _ensure_data_dir()
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump({"openclawDirectActive": _openclaw_direct_active}, f, indent=2)


def load() -> None:
    """(Re)load channels.json + config.json from disk."""
    global _channels, _openclaw_direct_active, _loaded
    _ensure_data_dir()

    p = _channels_path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        _channels = [
            Channel(
                id=str(c.get("id")),
                name=str(c.get("name")),
                type=c.get("type"),
                tokenHash=str(c.get("tokenHash", "")),
                active=bool(c.get("active", False)),
                createdAt=str(c.get("createdAt", "")),
                builtin=c.get("builtin"),
            )
            for c in raw
        ]
    else:
        _channels = []

    cp = _config_path()
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            cfg = json.load(f)
        _openclaw_direct_active = bool(cfg.get("openclawDirectActive", False))
    elif get_config().openclaw.url and not _channels:
        # First-run default: openclaw-direct active when URL configured.
        _openclaw_direct_active = True
        _save_config()
    else:
        _openclaw_direct_active = False

    _loaded = True


def _generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_hex(TOKEN_HEX_LEN // 2)


def _openclaw_direct_channel() -> Channel | None:
    if not get_config().openclaw.url:
        return None
    return Channel(
        id="openclaw-direct",
        name="OpenClaw Direct",
        type="openclaw-direct",
        tokenHash="",
        active=_openclaw_direct_active,
        createdAt=datetime.fromtimestamp(0, tz=timezone.utc).isoformat().replace("+00:00", "Z").replace("Z", ".000Z"),
        builtin=True,
    )


def _public(c: Channel) -> ChannelPublic:
    return ChannelPublic(
        id=c.id, name=c.name, type=c.type, active=c.active, createdAt=c.createdAt, builtin=c.builtin
    )


def get_all() -> list[ChannelPublic]:
    out: list[ChannelPublic] = []
    direct = _openclaw_direct_channel()
    if direct is not None:
        out.append(_public(direct))
    for c in _channels:
        out.append(_public(c))
    return out


def get_by_id(channel_id: str) -> Channel | None:
    if channel_id == "openclaw-direct":
        return _openclaw_direct_channel()
    for c in _channels:
        if c.id == channel_id:
            return c
    return None


def get_active() -> Channel | None:
    direct = _openclaw_direct_channel()
    if direct is not None and direct.active:
        return direct
    for c in _channels:
        if c.active:
            return c
    return None


async def create(name: str, type_: str = "openclaw") -> tuple[ChannelPublic, str]:
    if type_ != "openclaw":
        raise ValueError("invalid type, must be 'openclaw'")
    token = _generate_token()
    token_hash = await asyncio.to_thread(
        bcrypt.hashpw, token.encode("utf-8"), bcrypt.gensalt(BCRYPT_COST)
    )
    channel = Channel(
        id=str(uuid.uuid4()),
        name=name,
        type="openclaw",
        tokenHash=token_hash.decode("utf-8"),
        active=False,
        createdAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    _channels.append(channel)
    _save_channels()
    return _public(channel), token


def remove(channel_id: str) -> bool:
    global _channels
    if channel_id == "openclaw-direct":
        return False
    before = len(_channels)
    _channels = [c for c in _channels if c.id != channel_id]
    if len(_channels) == before:
        return False
    _save_channels()
    return True


def activate(channel_id: str) -> bool:
    global _openclaw_direct_active
    if channel_id == "openclaw-direct":
        direct = _openclaw_direct_channel()
        if direct is None:
            return False
        for c in _channels:
            c.active = False
        _save_channels()
        _openclaw_direct_active = True
        _save_config()
        return True

    target = next((c for c in _channels if c.id == channel_id), None)
    if target is None:
        return False
    for c in _channels:
        c.active = False
    _openclaw_direct_active = False
    _save_config()
    target.active = True
    _save_channels()
    return True


async def rotate_token(channel_id: str) -> str | None:
    if channel_id == "openclaw-direct":
        return None
    target = next((c for c in _channels if c.id == channel_id), None)
    if target is None:
        return None
    token = _generate_token()
    target.tokenHash = (
        await asyncio.to_thread(bcrypt.hashpw, token.encode("utf-8"), bcrypt.gensalt(BCRYPT_COST))
    ).decode("utf-8")
    _save_channels()
    return token


async def validate_channel_token(raw_token: str) -> Channel | None:
    for c in _channels:
        ok = await asyncio.to_thread(bcrypt.checkpw, raw_token.encode("utf-8"), c.tokenHash.encode("utf-8"))
        if ok:
            return c
    return None


# --- Test helpers ---


def _reset_for_tests() -> None:
    global _channels, _openclaw_direct_active, _loaded
    _channels = []
    _openclaw_direct_active = False
    _loaded = False


def _set_active_for_tests(channel: Channel | None) -> None:
    """Used in pipeline tests where we don't want disk I/O."""
    global _channels, _openclaw_direct_active
    if channel is None:
        for c in _channels:
            c.active = False
        _openclaw_direct_active = False
        return
    if channel.id == "openclaw-direct":
        for c in _channels:
            c.active = False
        _openclaw_direct_active = True
        return
    # Insert if missing so get_active() finds it.
    if not any(c.id == channel.id for c in _channels):
        _channels.append(channel)
    for c in _channels:
        c.active = c.id == channel.id
    _openclaw_direct_active = False
