"""Server-owned speech registry and atomic, dynamically inherited selections.

Each backend ID identifies one configured model deployment. Endpoint/voice wire
mapping belongs to the Wyoming adapter; management clients only see opaque IDs.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict

from config import Config, get_config


@dataclass(frozen=True)
class Backend:
    id: str
    kind: str
    adapter: str
    model: str
    host: str
    port: int
    voices: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    stt: Backend
    tts: Backend
    voice_id: str


class Settings(TypedDict, total=False):
    stt_backend: str
    tts_backend: str
    voices: dict[str, str]


class SpeechStore:
    def __init__(self, directory: Path, backends: tuple[Backend, ...]) -> None:
        self.path = directory / "speech-settings.json"
        self.backends = {b.id: b for b in backends}
        if len(self.backends) != len(backends) or len(backends) > 32:
            raise ValueError("Speech registry needs unique IDs and at most 32 backends")
        for b in backends:
            allowed = {"stt": {"whisper", "parakeet-v3"}, "tts": {"piper", "kokoro"}}
            if (
                b.adapter not in allowed.get(b.kind, set())
                or not b.id
                or not b.model
                or not b.host
                or type(b.port) is not int
                or not 1 <= b.port <= 65535
                or (b.kind == "tts" and not b.voices)
                or any(not isinstance(v, str) or not v for v in b.voices)
            ):
                raise ValueError("Invalid speech backend")
        self.defaults: Settings = {
            "stt_backend": "whisper",
            "tts_backend": "piper",
            "voices": {b.id: b.voices[0] for b in backends if b.kind == "tts"},
        }
        self.devices: dict[str, Settings] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.defaults = data["defaults"]
            self.devices = data["devices"]
        # Retain removed IDs in persisted settings: resolution fails explicitly,
        # rather than silently changing a device's selected provider.

    def resolve(self, device_id: str = "") -> Selection:
        override = self.devices.get(device_id, {})
        stt = self.backends[override.get("stt_backend", self.defaults["stt_backend"])]
        tts = self.backends[override.get("tts_backend", self.defaults["tts_backend"])]
        voice = override.get("voices", {}).get(tts.id, self.defaults["voices"].get(tts.id, tts.voices[0]))
        if voice not in tts.voices:
            raise ValueError("Selected voice is no longer configured")
        return Selection(stt, tts, voice)

    def update(self, patch: object, device_id: str | None = None) -> None:
        if not isinstance(patch, dict) or set(patch) - {"stt_backend", "tts_backend", "voices"}:
            raise ValueError("Expected stt_backend, tts_backend and/or voices")
        defaults = json.loads(json.dumps(self.defaults))
        devices = json.loads(json.dumps(self.devices))
        target = defaults if device_id is None else devices.setdefault(device_id, {})
        for key, value in patch.items():
            if value is None and device_id is not None:
                target.pop(key, None)
            elif key == "voices":
                if not isinstance(value, dict):
                    raise ValueError("voices must map TTS backend IDs to voice IDs")
                voices = target.setdefault("voices", {})
                for backend_id, voice in value.items():
                    b = self.backends.get(backend_id)
                    if b is None or b.kind != "tts":
                        raise ValueError("Unknown TTS backend")
                    if voice is None and device_id is not None:
                        voices.pop(backend_id, None)
                    elif not isinstance(voice, str) or voice not in b.voices:
                        raise ValueError("Voice does not belong to configured model")
                    else:
                        voices[backend_id] = voice
            else:
                b = self.backends.get(value) if isinstance(value, str) else None
                if b is None or b.kind != key[:3]:
                    raise ValueError("Unknown or incompatible speech backend")
                target[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"defaults": defaults, "devices": devices}, indent=2) + "\n")
        temporary.replace(self.path)
        self.defaults, self.devices = defaults, devices

    def view(self, device_id: str | None = None) -> dict[str, object]:
        try:
            selected = self.resolve(device_id or "")
            effective = {
                "stt_backend": selected.stt.id,
                "tts_backend": selected.tts.id,
                "voice_id": selected.voice_id,
            }
            error = None
        except (KeyError, ValueError):
            effective, error = None, "Selected backend or voice is no longer configured"
        return {
            "defaults": self.defaults,
            "overrides": self.devices.get(device_id, {}),
            "effective": effective,
            "error": error,
            "backends": [
                {k: v for k, v in asdict(b).items() if k not in {"host", "port"}}
                for b in self.backends.values()
            ],
        }


_store: SpeechStore | None = None
_store_config: Config | None = None


def get_store() -> SpeechStore:
    global _store, _store_config
    cfg = get_config()
    directory = Path(cfg.data_dir)
    if _store is None or _store_config is not cfg:
        backends = [
            Backend("whisper", "stt", "whisper", "legacy-whisper", cfg.whisper.host, cfg.whisper.port),
            Backend(
                "piper", "tts", "piper", cfg.piper.voice, cfg.piper.host, cfg.piper.port, (cfg.piper.voice,)
            ),
        ]
        path = directory / "speech-providers.json"
        if path.exists():
            for item in json.loads(path.read_text()):
                item["voices"] = tuple(item.get("voices", []))
                backends.append(Backend(**item))
        _store = SpeechStore(directory, tuple(backends))
        _store_config = cfg
    return _store


def resolve(device_id: str = "") -> Selection:
    return get_store().resolve(device_id)


async def readiness(backend: Backend) -> str:
    """Bounded Wyoming describe probe; not an inference or hardware benchmark."""
    from wyoming_stt import WyomingEvent, encode_event, parse_wyoming_events

    writer = None
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_connection(backend.host, backend.port)
            writer.write(encode_event(WyomingEvent("describe")))
            await writer.drain()
            buf = b""
            received = 0
            while received < 262144:
                data = await reader.read(8192)
                if not data:
                    break
                received += len(data)
                events, buf = parse_wyoming_events(buf + data)
                for event in events:
                    if event.type == "info":
                        return (
                            "ready"
                            if event.data.get("asr" if backend.kind == "stt" else "tts")
                            else "unavailable"
                        )
        return "unavailable"
    except (OSError, TimeoutError, ValueError, TypeError):
        return "unavailable"
    finally:
        if writer is not None:
            writer.close()
