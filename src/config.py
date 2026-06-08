"""Configuration loaded from environment variables.

Mirrors `src/config.ts` exactly: same env var names, same defaults, same
parsing rules.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WyomingEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class OpenClawConfig:
    url: str
    token: str


@dataclass(frozen=True)
class ChannelConfig:
    ws_path: str


@dataclass(frozen=True)
class DeviceConfigSection:
    token: str


@dataclass(frozen=True)
class PiperConfig:
    host: str
    port: int
    voice: str


@dataclass(frozen=True)
class PortConfig:
    port: int


@dataclass(frozen=True)
class StreamingTtsConfig:
    idle_pause_ms: int


@dataclass(frozen=True)
class RealtimeConfig:
    """WebRTC realtime (Pipecat) transport settings.

    Disabled by default — the realtime path and its pipecat/aiortc dependency
    only load when `enabled` is true, so the core WS server is unaffected.
    """

    enabled: bool
    esp32_mode: bool
    host: str
    stun_url: str
    offer_path: str


@dataclass(frozen=True)
class Config:
    openclaw: OpenClawConfig
    channel: ChannelConfig
    device: DeviceConfigSection
    data_dir: str
    whisper: WyomingEndpoint
    piper: PiperConfig
    ws: PortConfig
    http: PortConfig
    streaming_tts: StreamingTtsConfig
    realtime: RealtimeConfig
    log_level: str


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _optional(name: str, fallback: str) -> str:
    return os.environ.get(name) or fallback


def _parse_wyoming_url(raw: str) -> WyomingEndpoint:
    stripped = raw.removeprefix("tcp://")
    host, _, port_str = stripped.partition(":")
    return WyomingEndpoint(host=host, port=int(port_str))


def load_config() -> Config:
    return Config(
        openclaw=OpenClawConfig(
            url=_optional("OPENCLAW_URL", ""),
            token=_optional("OPENCLAW_TOKEN", ""),
        ),
        channel=ChannelConfig(ws_path="/channel"),
        device=DeviceConfigSection(token=_required("DEVICE_TOKEN")),
        data_dir=_optional("DATA_DIR", "/data"),
        whisper=_parse_wyoming_url(_optional("WHISPER_URL", "tcp://whisper:10300")),
        piper=PiperConfig(
            **_parse_wyoming_url(_optional("PIPER_URL", "tcp://piper:10200")).__dict__,
            voice=_optional("PIPER_VOICE", "en_US-libritts_r-medium"),
        ),
        ws=PortConfig(port=int(_optional("WS_PORT", "8765"))),
        http=PortConfig(port=int(_optional("HTTP_PORT", "8080"))),
        streaming_tts=StreamingTtsConfig(
            idle_pause_ms=int(_optional("STREAMING_TTS_IDLE_PAUSE_MS", "400")),
        ),
        realtime=RealtimeConfig(
            enabled=_optional("REALTIME_ENABLED", "0").lower() in ("1", "true", "yes"),
            esp32_mode=_optional("REALTIME_ESP32", "1").lower() in ("1", "true", "yes"),
            host=_optional("REALTIME_HOST", ""),
            stun_url=_optional("REALTIME_STUN_URL", "stun:stun.l.google.com:19302"),
            offer_path=_optional("REALTIME_OFFER_PATH", "/api/offer"),
        ),
        log_level=_optional("LOG_LEVEL", "info"),
    )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    global _config
    _config = None
