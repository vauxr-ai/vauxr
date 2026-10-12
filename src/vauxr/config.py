"""Server settings from config.json; environment values bootstrap absent settings."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

from vauxr.config_files import invalid, read_json, update_json
from vauxr.speech.catalog import speech_env
from vauxr.tls import TLSConfig, load_tls_config


@dataclass(frozen=True)
class WyomingEndpoint:
    host: str
    port: int


@dataclass(frozen=True)
class OpenClawConfig:
    url: str
    token: str


@dataclass(frozen=True)
class AgentConfig:
    ws_path: str


@dataclass(frozen=True)
class DeviceConfigSection:
    token: str


@dataclass(frozen=True)
class WyomingTTSConfig:
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
    # dB applied to GPT-Live spoken audio so it matches Standard-mode TTS loudness.
    output_gain_db: float


@dataclass(frozen=True)
class Config:
    openclaw: OpenClawConfig
    agent: AgentConfig
    device: DeviceConfigSection
    data_dir: str
    stt: WyomingEndpoint
    tts: WyomingTTSConfig
    ws: PortConfig
    http: PortConfig
    streaming_tts: StreamingTtsConfig
    realtime: RealtimeConfig
    log_level: str
    tls: TLSConfig = field(default_factory=TLSConfig)
    device_id_length: int = 16


def _optional(name: str, fallback: str) -> str:
    return os.environ.get(name) or fallback


def _parse_wyoming_url(raw: str) -> WyomingEndpoint:
    stripped = raw.removeprefix("tcp://")
    host, _, port_str = stripped.partition(":")
    return WyomingEndpoint(host=host, port=int(port_str))


Setting = TypeVar("Setting")


def load_config() -> Config:
    path = Path(_optional("DATA_DIR", "/data")) / "config.json"
    data = read_json(path) if path.exists() else {}
    if not isinstance(data, dict) or data.keys() - {"server", "pairing", "openclaw", "realtime"}:
        raise invalid(
            path,
            "$",
            "expected server, pairing, openclaw and/or realtime sections; recreate legacy configuration",
        )
    for section in ("server", "pairing", "openclaw", "realtime"):
        if section in data and not isinstance(data[section], dict):
            raise invalid(path, section, "expected an object")
    server = data.get("server", {})
    if server.keys() - {
        "ws_port",
        "http_port",
        "log_level",
        "streaming_tts_idle_pause_ms",
        "device_id_length",
    }:
        raise invalid(path, "server", "unknown setting; see docs/configuration.md")

    def setting(
        section: dict, key: str, env: str, fallback: str, convert: Callable[[str], Setting]
    ) -> Setting:
        if key in section:
            return section[key]
        try:
            return convert(_optional(env, fallback))
        except (ValueError, OverflowError):
            requirement = (
                "expected an integer from 1 to 64"
                if env == "DEVICE_ID_LENGTH"
                else "invalid environment setting; see docs/configuration.md"
            )
            raise invalid(path, env, requirement) from None

    def flag(value: str) -> bool:
        if value.lower() not in ("1", "true", "yes", "0", "false", "no"):
            raise ValueError("expected boolean")
        return value.lower() in ("1", "true", "yes")

    length = setting(server, "device_id_length", "DEVICE_ID_LENGTH", "16", int)
    if type(length) is not int or not 1 <= length <= 64:
        raise invalid(path, "server.device_id_length / DEVICE_ID_LENGTH", "expected an integer from 1 to 64")
    for key in ("ws_port", "http_port"):
        if key in server and (type(server[key]) is not int or not 1 <= server[key] <= 65535):
            raise invalid(path, "server." + key, "expected an integer from 1 to 65535")
    if "streaming_tts_idle_pause_ms" in server and (
        type(server["streaming_tts_idle_pause_ms"]) is not int or server["streaming_tts_idle_pause_ms"] < 0
    ):
        raise invalid(path, "server.streaming_tts_idle_pause_ms", "expected a nonnegative integer")
    if "log_level" in server and server["log_level"] not in ("debug", "info", "warning", "error", "critical"):
        raise invalid(path, "server.log_level", "expected debug, info, warning, error or critical")
    direct = data.get("openclaw", {})
    if direct.keys() - {"url"} or ("url" in direct and not isinstance(direct["url"], str)):
        raise invalid(path, "openclaw.url", "expected a URL string; credentials belong outside config.json")
    realtime = data.get("realtime", {})
    if realtime.keys() - {"enabled", "esp32_mode", "host", "stun_url", "offer_path", "output_gain_db"}:
        raise invalid(path, "realtime", "unknown setting; see docs/configuration.md")
    for key in ("enabled", "esp32_mode"):
        if key in realtime and type(realtime[key]) is not bool:
            raise invalid(path, "realtime." + key, "expected a boolean")
    for key in ("host", "stun_url", "offer_path"):
        if key in realtime and not isinstance(realtime[key], str):
            raise invalid(path, "realtime." + key, "expected a string")
    if "output_gain_db" in realtime and (
        type(realtime["output_gain_db"]) not in (int, float) or not math.isfinite(realtime["output_gain_db"])
    ):
        raise invalid(path, "realtime.output_gain_db", "expected a finite number")
    pairing = data.get("pairing", {})
    if pairing.keys() - {"prompts"}:
        raise invalid(path, "pairing", "expected prompts only")
    from vauxr.provisioning.pairing_audio import DEFAULT_PROMPTS, validate_prompts

    try:
        validate_prompts(pairing.get("prompts", DEFAULT_PROMPTS.copy()))
    except ValueError:
        raise invalid(
            path,
            "pairing.prompts",
            "requires valid intro and code strings, with {code} exactly once in code only",
        ) from None
    cfg = Config(
        openclaw=OpenClawConfig(
            url=direct.get("url", _optional("OPENCLAW_URL", "")),
            token=_optional("OPENCLAW_TOKEN", ""),
        ),
        agent=AgentConfig(ws_path="/agent"),
        device=DeviceConfigSection(token=_optional("DEVICE_TOKEN", "")),
        data_dir=_optional("DATA_DIR", "/data"),
        stt=_parse_wyoming_url(speech_env("STT_URL")),
        tts=WyomingTTSConfig(
            **_parse_wyoming_url(speech_env("TTS_URL")).__dict__,
            voice=speech_env("TTS_VOICE"),
        ),
        ws=PortConfig(port=setting(server, "ws_port", "WS_PORT", "8765", int)),
        http=PortConfig(port=setting(server, "http_port", "HTTP_PORT", "8080", int)),
        streaming_tts=StreamingTtsConfig(
            idle_pause_ms=setting(
                server, "streaming_tts_idle_pause_ms", "STREAMING_TTS_IDLE_PAUSE_MS", "1000", int
            ),
        ),
        realtime=RealtimeConfig(
            enabled=setting(realtime, "enabled", "REALTIME_ENABLED", "0", flag),
            esp32_mode=setting(realtime, "esp32_mode", "REALTIME_ESP32", "1", flag),
            host=realtime.get("host", _optional("REALTIME_HOST", "")),
            stun_url=realtime.get("stun_url", _optional("REALTIME_STUN_URL", "stun:stun.l.google.com:19302")),
            offer_path=realtime.get("offer_path", _optional("REALTIME_OFFER_PATH", "/api/offer")),
            output_gain_db=setting(realtime, "output_gain_db", "REALTIME_OUTPUT_GAIN_DB", "6", float),
        ),
        log_level=server.get("log_level", _optional("LOG_LEVEL", "info")).lower(),
        device_id_length=length,
        tls=load_tls_config(_optional("DATA_DIR", "/data")),
    )

    if not 1 <= cfg.ws.port <= 65535 or not 1 <= cfg.http.port <= 65535:
        raise invalid(path, "server.ws_port/http_port", "ports must be from 1 to 65535")
    if cfg.ws.port == cfg.http.port or (cfg.tls.enabled and cfg.tls.port in (cfg.ws.port, cfg.http.port)):
        raise invalid(path, "server.ws_port/http_port", "listener ports must be distinct")
    if cfg.openclaw.url:
        try:
            endpoint = urlsplit(cfg.openclaw.url)
        except ValueError:
            raise invalid(path, "openclaw.url", "expected a valid WebSocket endpoint URL") from None
        if (
            endpoint.scheme not in ("ws", "wss", "http", "https")
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
        ):
            raise invalid(
                path, "openclaw.url", "expected ws/wss/http/https with a host and no embedded credentials"
            )
    if cfg.streaming_tts.idle_pause_ms < 0 or not math.isfinite(cfg.realtime.output_gain_db):
        raise invalid(path, "server/realtime", "idle pause must be nonnegative and gain must be finite")
    if cfg.log_level.lower() not in ("debug", "info", "warning", "error", "critical"):
        raise invalid(path, "server.log_level", "expected debug, info, warning, error or critical")
    return cfg


def initialize_config() -> None:
    cfg = get_config()
    path = Path(cfg.data_dir) / "config.json"
    from vauxr.provisioning.pairing_audio import DEFAULT_PROMPTS

    prompts = DEFAULT_PROMPTS.copy()
    if not path.exists():
        update_json(
            path,
            lambda existing: (
                existing
                or {
                    "server": {
                        "ws_port": cfg.ws.port,
                        "http_port": cfg.http.port,
                        "log_level": cfg.log_level,
                        "streaming_tts_idle_pause_ms": cfg.streaming_tts.idle_pause_ms,
                    },
                    "pairing": {"prompts": prompts},
                    "openclaw": {"url": cfg.openclaw.url},
                    "realtime": {
                        key: getattr(cfg.realtime, key)
                        for key in (
                            "enabled",
                            "esp32_mode",
                            "host",
                            "stun_url",
                            "offer_path",
                            "output_gain_db",
                        )
                    },
                }
            ),
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
