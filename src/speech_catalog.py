"""Bounded server-owned Wyoming backend catalog and legacy deployment mapping.

Model labels describe deployments; no model loading or provisioning occurs here.
All supported adapters currently share Wyoming's voice.name wire contract.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from speech_models import Backend, Selection
from wyoming_protocol import WyomingEvent

if TYPE_CHECKING:
    from config import Config


_LEGACY_ENV = {
    "STT_URL": ("WHISPER_URL", "tcp://whisper:10300"),
    "TTS_URL": ("PIPER_URL", "tcp://piper:10200"),
    "TTS_VOICE": ("PIPER_VOICE", "en_US-libritts_r-medium"),
}
_ADAPTERS = {"stt": {"wyoming", "whisper", "parakeet-v3"}, "tts": {"wyoming", "piper", "kokoro"}}


def speech_env(name: str) -> str:
    """Neutral env names take precedence; empty values retain legacy fallback."""
    legacy, default = _LEGACY_ENV[name]
    return os.environ.get(name) or os.environ.get(legacy) or default


def validate_backend(backend: Backend) -> None:
    if (
        backend.adapter not in _ADAPTERS.get(backend.kind, set())
        or not backend.id
        or not backend.model
        or not backend.host
        or type(backend.port) is not int
        or not 1 <= backend.port <= 65535
        or (backend.kind == "tts" and not backend.voices)
        or any(not isinstance(v, str) or not v for v in backend.voices)
    ):
        raise ValueError("Invalid speech backend")


def load_backends(cfg: Config) -> tuple[Backend, ...]:
    # Stable IDs and ordering preserve existing persisted selections and defaults,
    # even when an operator migrates to the neutral environment variable names.
    backends = [
        Backend("whisper", "stt", "whisper", "legacy-whisper", cfg.stt.host, cfg.stt.port),
        Backend("piper", "tts", "piper", cfg.tts.voice, cfg.tts.host, cfg.tts.port, (cfg.tts.voice,)),
    ]
    path = Path(cfg.data_dir) / "speech-providers.json"
    if path.exists():
        for item in json.loads(path.read_text()):
            item["voices"] = tuple(item.get("voices", []))
            backends.append(Backend(**item))
    return tuple(backends)


def synthesis_event(text: str, selection: Selection) -> WyomingEvent:
    """Translate a configured voice selection to the supported adapter contract."""
    return WyomingEvent("synthesize", {"text": text, "voice": {"name": selection.voice_id}})
