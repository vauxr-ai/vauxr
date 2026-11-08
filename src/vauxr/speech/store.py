"""Server-owned speech registry and atomic, dynamically inherited selections.

Each backend ID identifies one configured model deployment. Endpoint/voice wire
mapping belongs to the Wyoming adapter; management clients only see opaque IDs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
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
    mode: str | None
    realtime_backend: str
    realtime_voice: str


class VoiceConflict(ValueError):
    """A valid selection cannot run until its named conflict is resolved."""


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
            or not {"version", "providers", "defaults", "devices"} <= data.keys()
            or data.keys()
            - {"version", "providers", "defaults", "devices", "agents", "assignments", "capabilities"}
            or type(data["version"]) is not int
            or data["version"] not in (1, 2)
        ):
            raise invalid(self.path, "$", "expected version 1 or 2, providers, defaults and devices")
        self.backends = {b.id: b for b in parse_backends(data["providers"], self.path)}
        self.defaults, self.devices = data["defaults"], data["devices"]
        self.agents = data.get("agents", {})
        self.assignments = data.get("assignments", {})
        self.capabilities = data.get("capabilities", {})
        if not all(isinstance(value, dict) for value in (self.agents, self.assignments, self.capabilities)):
            raise invalid(self.path, "agents/assignments/capabilities", "expected objects")
        for policy in self.agents.values():
            self._validate_policy(policy)
        if not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in self.assignments.items()
        ):
            raise invalid(self.path, "assignments", "expected device IDs mapped to agent IDs")
        if not all(
            isinstance(value, list) and all(cap in ("ws", "webrtc") for cap in value)
            for value in self.capabilities.values()
        ):
            raise invalid(self.path, "capabilities", "expected verified transport capabilities")
        if not isinstance(self.defaults, dict) or not isinstance(self.devices, dict):
            raise invalid(self.path, "defaults/devices", "expected objects")
        self._validate_settings(self.defaults, "defaults", False)
        for device_id, settings in self.devices.items():
            self._validate_settings(settings, "devices.*", True)
            # Revocation/removal must not make otherwise valid stored overrides
            # unloadable. Missing routing authority is reported as a conflict.
            from vauxr.agents import registry

            if registry.get_by_id(self.agent_id(device_id)) is not None or not (
                self.agents or device_id in self.assignments
            ):
                self._validate_device_effective(device_id)

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

    def _validate_policy(self, policy: object) -> None:
        if not isinstance(policy, dict) or set(policy) != {"enabled_modes", "default_mode", "selection"}:
            raise ValueError("Agent voice policy requires enabled_modes, default_mode and selection")
        modes = policy["enabled_modes"]
        if (
            not isinstance(modes, list)
            or any(not isinstance(mode, str) or mode not in ("standard", "realtime") for mode in modes)
            or len(set(modes)) != len(modes)
        ):
            raise ValueError("Choose supported voice modes, or disable both for chat-only")
        if (modes and policy["default_mode"] not in modes) or (not modes and policy["default_mode"] is not None):
            raise ValueError("Default mode must be enabled, or null for chat-only")
        self._validate_settings(policy["selection"], "agents.*.selection", True)
        if "mode" in policy["selection"]:
            raise ValueError("Use default_mode for agent voice policy")
        for mode in modes:
            try:
                settings = {**self.defaults, **policy["selection"], "mode": mode}
                if mode == "standard":
                    self._validate_settings(
                        {key: value for key, value in settings.items() if not key.startswith("realtime_")},
                        "agents.*.standard",
                        False,
                    )
                else:
                    self._validate_effective(settings, "agents.*.realtime")
            except (KeyError, ValueError) as error:
                label = "Live" if mode == "realtime" else "Standard"
                raise ValueError(
                    f"Configure a registered {label} provider and matching voice before enabling {label}. {error}"
                ) from error

        # Declared inactive-mode fields must remain internally valid as well;
        # retaining an unusable voice/provider pair would break later inheritance.
        self._validate_effective(
            {**self.defaults, **policy["selection"], "mode": policy["default_mode"]}, "agents.*"
        )

    def agent_id(self, device_id: str) -> str:
        from vauxr.agents import registry

        if device_id in self.assignments:
            return self.assignments[device_id]
        active = registry.get_active()
        return active.id if active else ""

    def integration_modes(self, agent_id: str) -> list[str]:
        from vauxr.agents import registry

        agent = registry.get_by_id(agent_id)
        return ["standard", "realtime"] if agent and agent.type == "openclaw" else ["standard"]

    def chat_only_agent(self, agent_id: str) -> bool:
        return self.agents.get(agent_id, {}).get("enabled_modes") == []

    def chat_only(self, device_id: str) -> bool:
        return self.chat_only_agent(self.agent_id(device_id))

    @staticmethod
    def no_voice_message() -> str:
        return "Chat-only agent: no voice modes enabled. Browser text is available; choose a voice-enabled agent for physical voice devices."

    @staticmethod
    def _local_standard(agent_id: str) -> bool:
        from vauxr.agents import registry
        agent = registry.get_by_id(agent_id)
        return agent is not None and agent.type == "langgraph"

    def agent_defaults(self, agent_id: str) -> dict:
        policy = self.agents.get(agent_id)
        if not policy:
            return {**self.defaults, "mode": "standard"} if (agent_id == "vauxr-support" or self._local_standard(agent_id)) else dict(self.defaults)
        selection = policy["selection"]
        return {
            **self.defaults,
            **selection,
            "mode": policy["default_mode"],
            "voices": {**self.defaults["voices"], **selection.get("voices", {})},
        }

    def inherited(self, device_id: str) -> dict:
        return self.agent_defaults(self.agent_id(device_id)) if device_id else dict(self.defaults)

    def managed(self, device_id: str) -> bool:
        return (
            device_id in self.assignments
            or self.agent_id(device_id) == "vauxr-support"
            or self._local_standard(self.agent_id(device_id))
            or self.agent_id(device_id) in self.agents
            or (bool(self.agents) and not self.agent_id(device_id))
        )

    def available_modes(self, device_id: str) -> list[str]:
        agent_id = self.agent_id(device_id)
        integration = self.integration_modes(agent_id)
        enabled = self.agents.get(agent_id, {}).get("enabled_modes", integration)
        supported = (
            ["standard", "realtime"] if "webrtc" in self.capabilities.get(device_id, []) else ["standard"]
        )
        return [mode for mode in integration if mode in enabled and mode in supported]

    def _validate_device_effective(self, device_id: str) -> None:
        if self.chat_only(device_id):
            return  # Stored overrides stay dormant; none can enable voice.
        inherited = self.inherited(device_id)
        override = self.devices.get(device_id, {})
        settings = {**inherited, **override, "voices": {**inherited["voices"], **override.get("voices", {})}}
        self._validate_settings(settings, f"devices.{device_id}", False)

    def conflict(self, device_id: str, *, transport: str | None = None) -> str | None:
        from vauxr.agents import registry

        agent_id = self.agent_id(device_id)
        if not agent_id or registry.get_by_id(agent_id) is None:
            return "Assigned agent unavailable. Choose an available agent."
        if self.chat_only(device_id):
            from vauxr.auth.service import get_store

            browser = get_store().lifecycle.get("bindings", {}).get(device_id, {}).get("kind") == "browser"
            return None if browser else self.no_voice_message()
        try:
            self._validate_device_effective(device_id)
        except (KeyError, ValueError):
            return "Provider/voice overrides are incompatible with this agent. Select a matching provider and voice or reset voice overrides."
        mode = self.voice_settings(device_id)["mode"]
        if mode not in self.available_modes(device_id):
            return (
                f"Selected {mode} mode is unavailable. Enable it on the agent, choose a supported "
                "device override, or reset overrides to a compatible agent default."
            )
        if transport is None:
            from vauxr.devices.config import load_device_configs

            transport = (
                load_device_configs(str(self.path.parent)).get(device_id, {}).get("transport_mode", "webrtc")
            )
        if mode == "realtime" and transport == "websocket":
            return "Live requires WebRTC. Select WebRTC transport or a Standard override."
        return None

    def policy_view(self, device_id: str | None, agent_id: str | None) -> dict:
        from vauxr.agents import registry

        active = registry.get_active()
        identity = agent_id or self.agent_id(device_id or "")
        integration = self.integration_modes(identity)
        policy = self.agents.get(identity, {})
        return {
            "agents": [{"id": agent.id, "name": agent.name} for agent in registry.get_all()],
            "fallback_agent": active.name if active else "none",
            "agent_id": identity,
            "assigned": (device_id or "") in self.assignments,
            "configured": bool(policy),
            "chat_only": self.chat_only_agent(identity),
            "integration_modes": integration,
            "enabled_modes": policy.get("enabled_modes", integration),
            "default_mode": policy.get("default_mode", self.agent_defaults(identity)["mode"]),
            "available_modes": self.available_modes(device_id) if device_id else [
                mode for mode in integration if mode in policy.get("enabled_modes", integration)
            ],
            "capabilities_known": (device_id or "") in self.capabilities,
            "conflict": self.conflict(device_id) if device_id and self.managed(device_id) else None,
        }

    def runtime_conflict(self, device_id: str, mode: str) -> str | None:
        if not self.managed(device_id):
            return None
        if self.chat_only(device_id):
            return self.no_voice_message()
        conflict = self.conflict(device_id)
        if conflict:
            return conflict
        if self.voice_settings(device_id)["mode"] != mode:
            return "Voice mode changed. Stop voice and reconnect using the selected mode."
        return None

    def _selection_signature(self, device_id: str) -> tuple:
        settings = self.voice_settings(device_id)
        selected = None if self.chat_only(device_id) else (
            settings if settings["mode"] == "realtime" else self.resolve(device_id)
        )
        return self.agent_id(device_id), settings["mode"], selected

    def _active_change(self, candidate: SpeechStore, device_id: str) -> str | None:
        if self._selection_signature(device_id) == candidate._selection_signature(device_id):
            return None
        from vauxr.devices import registry

        device = registry.get(device_id)
        module = sys.modules.get("vauxr.realtime.session")
        manager = getattr(module, "_manager", None)
        chat = sys.modules.get("vauxr.chat")
        if chat is not None and chat.is_active(device_id):
            return "A text request is active. Wait for its result before changing the conversation agent or modes."
        if (
            device and device.state not in ("idle", "offline")
        ) or (
            manager is not None and manager.has_live_session(device_id)
        ):
            return "Voice is active. Stop voice and disconnect its WebRTC session, then retry this change."
        return None

    def validate_fallback(self, agent_id: str) -> None:
        """Validate a route change before publishing it in the agent registry."""
        from vauxr.devices.config import load_device_configs

        candidate = object.__new__(SpeechStore)
        candidate.__dict__ = {**self.__dict__, "assignments": dict(self.assignments)}
        for device_id in (
            set(self.devices) | set(self.capabilities) | set(load_device_configs(str(self.path.parent)))
        ):
            if device_id in self.assignments:
                continue
            candidate.assignments[device_id] = agent_id
            try:
                candidate._validate_device_effective(device_id)
            except (KeyError, ValueError) as error:
                raise VoiceConflict(
                    f"{device_id}: Provider/voice overrides are incompatible with the new fallback agent. "
                    "Select a matching provider and voice or reset voice overrides before activating it."
                ) from error
            if not (self.managed(device_id) or agent_id in self.agents):
                continue
            conflict = candidate.conflict(device_id) or self._active_change(candidate, device_id)
            if conflict:
                raise VoiceConflict(f"{device_id}: {conflict}")

    def record_capabilities(self, device_id: str, caps: list[str]) -> None:
        verified = sorted(set(caps) & {"ws", "webrtc"})
        if self.capabilities.get(device_id) == verified:
            return
        self._load(
            update_json(
                self.path,
                lambda data: {
                    **data,
                    "version": 2,
                    "capabilities": {**data.get("capabilities", {}), device_id: verified},
                },
            )
        )

    def resolve(self, device_id: str = "", agent_id: str | None = None) -> Selection:
        if (agent_id and self.chat_only_agent(agent_id)) or (device_id and self.chat_only(device_id)):
            raise VoiceConflict(self.no_voice_message())
        settings = self.agent_defaults(agent_id) if agent_id else self.inherited(device_id)
        override = self.devices.get(device_id, {})
        stt = self.backends[override.get("stt_backend", settings["stt_backend"])]
        tts = self.backends[override.get("tts_backend", settings["tts_backend"])]
        if stt.kind != "stt" or tts.kind != "tts":
            raise ValueError("Selected backend has incompatible speech kind")
        voice = override.get("voices", {}).get(tts.id, settings["voices"].get(tts.id, tts.voices[0]))
        if voice not in tts.voices:
            raise ValueError("Selected voice is no longer configured")
        return Selection(stt, tts, voice)

    def voice_settings(self, device_id: str = "", agent_id: str | None = None) -> dict[str, str | None]:
        settings = {
            **(self.agent_defaults(agent_id) if agent_id else self.inherited(device_id)),
            **self.devices.get(device_id, {}),
        }
        if (agent_id and self.chat_only_agent(agent_id)) or (device_id and self.chat_only(device_id)):
            return {"mode": None, "realtime_backend": "", "realtime_model": "", "realtime_voice": ""}
        provider = self.backends.get(settings.get("realtime_backend", ""))
        return {
            "mode": settings["mode"],
            "realtime_backend": provider.id if provider else "",
            "realtime_model": provider.model if provider else "",
            "realtime_voice": settings.get("realtime_voice", ""),
        }

    def update(
        self,
        patch: object,
        device_id: str | None = None,
        agent_id: str | None = None,
        transport: str | None = None,
    ) -> None:
        if not isinstance(patch, dict):
            raise invalid(self.path, "selection", "expected a speech selection object")

        def change(data: dict) -> dict:
            # Build and validate a separate candidate, preserving the live snapshot on failure.
            candidate = object.__new__(SpeechStore)
            candidate.path = self.path
            candidate._load(json.loads(json.dumps(data)))
            managed_patch = dict(patch)
            if "reset" in managed_patch and type(managed_patch["reset"]) is not bool:
                raise ValueError("reset must be a boolean")
            if agent_id is not None:
                from vauxr.agents import registry

                if registry.get_by_id(agent_id) is None:
                    raise ValueError("Agent unavailable; refresh agents")
                policy = candidate.agents.setdefault(
                    agent_id,
                    {
                        "enabled_modes": candidate.integration_modes(agent_id),
                        "default_mode": candidate.agent_defaults(agent_id)["mode"],
                        "selection": {},
                    },
                )
                for key in ("enabled_modes", "default_mode"):
                    if key in managed_patch:
                        policy[key] = managed_patch.pop(key)
                if policy["enabled_modes"] == [] and "default_mode" not in patch:
                    policy["default_mode"] = None
                target = policy["selection"]
            else:
                target = (
                    candidate.defaults if device_id is None else candidate.devices.setdefault(device_id, {})
                )
            if "agent_id" in managed_patch:
                from vauxr.agents import registry

                assigned = managed_patch.pop("agent_id")
                if device_id is None or (
                    assigned is not None
                    and (not isinstance(assigned, str) or registry.get_by_id(assigned) is None)
                ):
                    raise ValueError("Choose an available agent for this device")
                if assigned is None:
                    candidate.assignments.pop(device_id, None)
                else:
                    candidate.assignments[device_id] = assigned
            if managed_patch.pop("reset", False):
                if device_id is None:
                    raise ValueError("Reset is only available for device overrides")
                target.clear()
            for key, value in managed_patch.items():
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
            result = {
                **data,
                "version": 2,
                "defaults": candidate.defaults,
                "devices": candidate.devices,
                "agents": candidate.agents,
                "assignments": candidate.assignments,
                "capabilities": candidate.capabilities,
            }
            candidate._load(result)
            if device_id and candidate.chat_only(device_id) and (
                set(patch) - {"agent_id", "reset"}
            ):
                raise VoiceConflict(candidate.no_voice_message())
            if agent_id:
                policy = candidate.agents[agent_id]
                if set(policy["enabled_modes"]) - set(candidate.integration_modes(agent_id)):
                    raise ValueError("This integration does not support the selected voice mode")
            from vauxr.devices.config import load_device_configs

            affected = (
                set(load_device_configs(str(self.path.parent)))
                | set(candidate.devices)
                | set(candidate.assignments)
                | set(candidate.capabilities)
            )
            if device_id:
                affected.add(device_id)
            conflicts = []
            for identity in sorted(affected):
                if device_id and identity != device_id:
                    continue
                if agent_id and candidate.agent_id(identity) != agent_id:
                    continue
                # Legacy unmanaged selections remain unchanged until explicitly configured.
                if not (
                    agent_id
                    or candidate.managed(identity)
                    or self.managed(identity)
                    or (identity == device_id and "agent_id" in patch)
                ):
                    continue
                conflict = candidate.conflict(
                    identity, transport=transport if identity == device_id else None
                )
                conflict = conflict or self._active_change(candidate, identity)
                if conflict:
                    conflicts.append(f"{identity}: {conflict}")
            if conflicts:
                raise VoiceConflict("; ".join(conflicts))
            return result

        try:
            data = update_json(self.path, change)
        except OSError:
            self._load(read_json(self.path))
            raise
        self._load(data)

    def view(self, device_id: str | None = None, agent_id: str | None = None) -> dict[str, object]:
        try:
            selected = self.resolve(device_id or "", agent_id)
            effective = {
                "stt_backend": selected.stt.id,
                "tts_backend": selected.tts.id,
                "voice_id": selected.voice_id,
            }
            error = None
        except (KeyError, ValueError):
            effective = None
            chat_only = self.chat_only_agent(agent_id) if agent_id else (device_id and self.chat_only(device_id))
            error = None if chat_only else "Selected backend or voice is no longer configured"
        return {
            "voice": self.voice_settings(device_id or "", agent_id),
            "realtime": {
                "voices": list(
                    self.backends[self.voice_settings(device_id or "", agent_id)["realtime_backend"]].voices
                )
                if self.voice_settings(device_id or "", agent_id)["realtime_backend"]
                else [],
                "configured": bool(get_config().realtime.enabled and os.environ.get("OPENAI_API_KEY")
                                   and not self.policy_view(device_id, agent_id)["chat_only"]),
            },
            "defaults": self.agent_defaults(agent_id) if agent_id else self.inherited(device_id or ""),
            "overrides": self.devices.get(device_id, {}),
            "policy": self.policy_view(device_id, agent_id),
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
