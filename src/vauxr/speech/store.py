"""Server-owned speech registry and atomic, dynamically inherited selections.

Each backend ID identifies one configured model deployment. Endpoint/voice wire
mapping belongs to the Wyoming adapter; management clients only see opaque IDs.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

from vauxr.config import Config, get_config
from vauxr.config_files import invalid, read_json, update_json
from vauxr.speech.catalog import load_backends, parse_backends, validate_backend
from vauxr.speech.models import Backend, Selection


class Settings(TypedDict, total=False):
    stt_backend: str
    tts_backend: str
    voices: dict[str, str]
    mode: str
    realtime_backend: str
    realtime_voice: str


class SpeechStore:
    def __init__(self, directory: Path, backends: tuple[Backend, ...]) -> None:
        self.path = directory / "speech.json"
        if self.path.exists():
            self._load(read_json(self.path))
            return
        for backend in backends:
            validate_backend(backend)
        initial = {
            kind: next((b.id for b in backends if b.kind == kind), None)
            for kind in ("stt", "tts", "realtime")
        }
        data = {
            "version": 1,
            "providers": [asdict(b) for b in backends],
            "defaults": {
                "mode": "standard",
                "stt_backend": initial["stt"],
                "tts_backend": initial["tts"],
                "voices": {b.id: b.voices[0] for b in backends if b.kind == "tts"},
            },
            "devices": {},
        }
        if initial["realtime"]:
            data["defaults"].update(
                realtime_backend=initial["realtime"],
                realtime_voice=next(b.voices[0] for b in backends if b.id == initial["realtime"]),
            )
        # JSON conversion normalizes dataclass tuples to the persisted array schema.
        data = json.loads(json.dumps(data))
        self._load(data)
        self._load(update_json(self.path, lambda existing: existing or data))

    def _load(self, data: object) -> None:
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "providers", "defaults", "devices"}
            or type(data["version"]) is not int
            or data["version"] != 1
        ):
            raise invalid(self.path, "$", "expected version 1, providers, defaults and devices")
        self.backends = {b.id: b for b in parse_backends(data["providers"], self.path)}
        self.defaults, self.devices = data["defaults"], data["devices"]
        if not isinstance(self.defaults, dict) or not isinstance(self.devices, dict):
            raise invalid(self.path, "defaults/devices", "expected objects")
        self._validate_settings(self.defaults, "defaults", False)
        for settings in self.devices.values():
            self._validate_settings(settings, "devices.*", True)
            self._validate_effective({**self.defaults, **settings}, "devices.*")

    def _validate_settings(self, settings: object, field: str, override: bool) -> None:
        keys = {"stt_backend", "tts_backend", "voices", "mode", "realtime_backend", "realtime_voice"}
        if not isinstance(settings, dict) or settings.keys() - keys:
            raise invalid(self.path, field, "expected speech selection fields only")
        if not override and not {"stt_backend", "tts_backend", "voices", "mode"} <= settings.keys():
            raise invalid(self.path, field, "requires stt_backend, tts_backend, voices and mode")
        for key, kind in (("stt_backend", "stt"), ("tts_backend", "tts"), ("realtime_backend", "realtime")):
            if key in settings:
                value = settings[key]
                b = self.backends.get(value) if isinstance(value, str) else None
                if b is None or b.kind != kind:
                    raise invalid(
                        self.path, field + "." + key, "must reference a provider ID of the matching kind"
                    )
        if "mode" in settings and settings["mode"] not in ("standard", "realtime"):
            raise invalid(self.path, field + ".mode", "expected standard or realtime")
        voices = settings.get("voices", {})
        if not isinstance(voices, dict):
            raise invalid(self.path, field + ".voices", "expected a map of TTS provider IDs to voices")
        for provider, voice in voices.items():
            b = self.backends.get(provider)
            if b is None or b.kind != "tts" or not isinstance(voice, str) or voice not in b.voices:
                raise invalid(self.path, field + ".voices", "each voice must belong to its TTS provider")
        if "realtime_voice" in settings and not isinstance(settings["realtime_voice"], str):
            raise invalid(self.path, field + ".realtime_voice", "expected a voice string")
        if not override:
            self._validate_effective(settings, field)

    def _validate_effective(self, settings: dict, field: str) -> None:
        provider = settings.get("realtime_backend")
        if settings.get("mode") == "realtime" and not provider:
            raise invalid(
                self.path, field + ".realtime_backend", "realtime mode requires a registry provider ID"
            )
        if provider:
            b = self.backends[provider]
            if settings.get("realtime_voice") not in b.voices:
                raise invalid(
                    self.path, field + ".realtime_voice", "must belong to the selected realtime provider"
                )

    def resolve(self, device_id: str = "") -> Selection:
        override = self.devices.get(device_id, {})
        stt = self.backends[override.get("stt_backend", self.defaults["stt_backend"])]
        tts = self.backends[override.get("tts_backend", self.defaults["tts_backend"])]
        if stt.kind != "stt" or tts.kind != "tts":
            raise ValueError("Selected backend has incompatible speech kind")
        voice = override.get("voices", {}).get(tts.id, self.defaults["voices"].get(tts.id, tts.voices[0]))
        if voice not in tts.voices:
            raise ValueError("Selected voice is no longer configured")
        return Selection(stt, tts, voice)

    def voice_settings(self, device_id: str = "") -> dict[str, str]:
        settings = {**self.defaults, **self.devices.get(device_id, {})}
        provider = self.backends.get(settings.get("realtime_backend", ""))
        return {
            "mode": settings["mode"],
            "realtime_backend": provider.id if provider else "",
            "realtime_model": provider.model if provider else "",
            "realtime_voice": settings.get("realtime_voice", ""),
        }

    def update(self, patch: object, device_id: str | None = None) -> None:
        if not isinstance(patch, dict):
            raise invalid(self.path, "selection", "expected a speech selection object")

        def change(data: dict) -> dict:
            # Build and validate a separate candidate, preserving the live snapshot on failure.
            candidate = object.__new__(SpeechStore)
            candidate.path = self.path
            candidate._load(json.loads(json.dumps(data)))
            target = candidate.defaults if device_id is None else candidate.devices.setdefault(device_id, {})
            for key, value in patch.items():
                if key not in {
                    "stt_backend",
                    "tts_backend",
                    "voices",
                    "mode",
                    "realtime_backend",
                    "realtime_voice",
                }:
                    raise ValueError("Unknown speech selection field")
                if value is None and device_id is not None:
                    target.pop(key, None)
                elif key == "voices":
                    if not isinstance(value, dict):
                        raise ValueError("voices must map provider IDs to voice IDs")
                    voices = target.setdefault("voices", {})
                    for backend_id, voice in value.items():
                        if voice is None and device_id is not None:
                            voices.pop(backend_id, None)
                        else:
                            voices[backend_id] = voice
                else:
                    target[key] = value
            if device_id is not None and not target:
                candidate.devices.pop(device_id, None)
            result = {**data, "defaults": candidate.defaults, "devices": candidate.devices}
            candidate._load(result)
            return result

        try:
            data = update_json(self.path, change)
        except OSError:
            self._load(read_json(self.path))
            raise
        self._load(data)

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
            "voice": self.voice_settings(device_id or ""),
            "realtime": {
                "voices": list(self.backends[self.voice_settings(device_id or "")["realtime_backend"]].voices)
                if self.voice_settings(device_id or "")["realtime_backend"]
                else [],
                "configured": bool(get_config().realtime.enabled and os.environ.get("OPENAI_API_KEY")),
            },
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
        _store = SpeechStore(directory, load_backends(cfg))
        _store_config = cfg
    return _store


def resolve(device_id: str = "") -> Selection:
    return get_store().resolve(device_id)


async def readiness(backend: Backend) -> str:
    """Bounded provider probe; not an inference or hardware benchmark."""
    if backend.adapter == "openai-live":
        return (
            "configured"
            if get_config().realtime.enabled and os.environ.get("OPENAI_API_KEY")
            else "unavailable"
        )
    if backend.adapter == "openai":
        import vauxr.speech.openai as openai_tts

        return await openai_tts.readiness(backend)
    from vauxr.speech.wyoming_protocol import WyomingEvent, encode_event, parse_wyoming_events

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
