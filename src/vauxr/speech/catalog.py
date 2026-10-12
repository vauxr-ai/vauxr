"""Shipped speech deployments used to initialize speech.json on first run."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import vauxr.speech.openai as openai_tts
from vauxr.config_files import invalid, read_json
from vauxr.speech.models import Backend, Selection
from vauxr.speech.wyoming_protocol import WyomingEvent

if TYPE_CHECKING:
    from vauxr.config import Config

_LEGACY_ENV = {
    "STT_URL": ("WHISPER_URL", "tcp://whisper:10300"),
    "TTS_URL": ("PIPER_URL", "tcp://piper:10200"),
    "TTS_VOICE": ("PIPER_VOICE", "en_US-libritts_r-medium"),
}
_ADAPTERS = {
    "stt": {"wyoming", "whisper", "parakeet-v3"},
    "tts": {"wyoming", "piper", "kokoro", openai_tts.ADAPTER},
    "realtime": {"openai-live"},
}


def speech_env(name: str) -> str:
    legacy, default = _LEGACY_ENV[name]
    return os.environ.get(name) or os.environ.get(legacy) or default


def validate_backend(backend: Backend) -> None:
    if (
        backend.adapter not in _ADAPTERS.get(backend.kind, set())
        or not all(isinstance(v, str) and v for v in (backend.id, backend.model, backend.host))
        or type(backend.port) is not int
        or not 1 <= backend.port <= 65535
        or not isinstance(backend.voices, tuple)
        or (backend.kind in ("tts", "realtime") and not backend.voices)
        or any(not isinstance(v, str) or not v for v in backend.voices)
        or len(set(backend.voices)) != len(backend.voices)
        or (backend.adapter == "openai-live" and (backend.host != "api.openai.com" or backend.port != 443))
    ):
        raise ValueError("Invalid speech backend")


def shipped_backends(cfg: Config) -> tuple[Backend, ...]:
    # All shipped providers are visible even when their service/key is unavailable.
    return (
        Backend("whisper", "stt", "whisper", "legacy-whisper", cfg.stt.host, cfg.stt.port),
        Backend("piper", "tts", "piper", cfg.tts.voice, cfg.tts.host, cfg.tts.port, (cfg.tts.voice,)),
        Backend("parakeet-v3", "stt", "parakeet-v3", "parakeet-v3", "parakeet", 10300),
        Backend("kokoro", "tts", "kokoro", "kokoro", "kokoro", 10200, ("af_heart",)),
        Backend(
            "openai-tts",
            "tts",
            openai_tts.ADAPTER,
            openai_tts.MODEL,
            "api.openai.com",
            443,
            openai_tts.VOICES,
        ),
        Backend(
            "openai-live", "realtime", "openai-live", "gpt-live-1", "api.openai.com", 443, ("marin", "cedar")
        ),
    )


def parse_backends(raw: object, path: Path) -> tuple[Backend, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 32:
        raise invalid(path, "providers", "expected an array of 1–32 providers")
    backends = []
    for index, item in enumerate(raw):
        try:
            if not isinstance(item, dict) or not isinstance(item.get("voices", []), list):
                raise TypeError
            b = Backend(**{**item, "voices": tuple(item.get("voices", []))})
            validate_backend(b)
        except (TypeError, ValueError):
            raise invalid(
                path,
                f"providers[{index}]",
                "expected id, kind, supported adapter, model, host, port (1–65535), and voices for TTS/realtime; no credentials",
            ) from None
        backends.append(b)
    if len({b.id for b in backends}) != len(backends):
        raise invalid(path, "providers", "provider IDs must be unique")
    return tuple(backends)


def load_backends(cfg: Config) -> tuple[Backend, ...]:
    path = Path(cfg.data_dir) / "speech.json"
    if not path.exists():
        return shipped_backends(cfg)
    data = read_json(path)
    if not isinstance(data, dict):
        raise invalid(path, "$", "expected an object")
    return parse_backends(data.get("providers"), path)


def synthesis_event(text: str, selection: Selection) -> WyomingEvent:
    return WyomingEvent("synthesize", {"text": text, "voice": {"name": selection.voice_id}})
