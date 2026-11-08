"""Validated connection profiles; secrets never form part of a public view.

Runtime consumers call resolve_profile(id). Speech deployments retain their own
model/voice selections; sharing a profile shares authentication only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from vauxr.config import get_config
from vauxr.config_files import locked, read_json, update_json, write_json

ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


def directory() -> Path:
    return Path(get_config().data_dir)


def text(value: object, maximum: int = 200) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def validate_profile(profile: object) -> dict:
    if not isinstance(profile, dict) or set(profile) != {
        "id",
        "name",
        "provider",
        "endpoint",
        "model",
        "auth",
    }:
        raise ValueError("Profile requires id, name, provider, endpoint, model and auth")
    if not isinstance(profile["id"], str) or not ID.fullmatch(profile["id"]):
        raise ValueError("Invalid profile ID")
    if not all(text(profile[k]) for k in ("name", "model")):
        raise ValueError("Name and model must be nonempty bounded strings")
    if profile["provider"] not in ("ollama", "openai") or profile["auth"] not in (
        "none",
        "environment",
        "inline",
    ):
        raise ValueError("Unsupported provider or authentication method")
    endpoint = profile["endpoint"]
    try:
        url = urlsplit(endpoint) if text(endpoint, 500) else None
        if (
            url is None
            or url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.port == 0
        ):
            raise ValueError
        if profile["provider"] == "openai" and endpoint != "https://api.openai.com":
            raise ValueError
        if profile["provider"] == "ollama" and profile["auth"] != "none":
            raise ValueError
        if profile["provider"] == "openai" and profile["auth"] == "none":
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(
            "Use an HTTP(S) Ollama endpoint without credentials, or https://api.openai.com for OpenAI"
        ) from None
    return dict(profile)


class SecretStore:
    """Separate atomic JSON store. mkstemp + replacement keeps writes mode 0600."""

    def __init__(self, root: Path) -> None:
        self.path = root / "settings-secrets.json"

    def read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        data = read_json(self.path)
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "values"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["values"], dict)
            or not all(isinstance(k, str) and text(v, 8192) for k, v in data["values"].items())
        ):
            raise ValueError("Invalid secret store schema")
        os.chmod(self.path, 0o600)
        return data["values"]

    def change(self, replacements: dict[str, str], clear: list[str]) -> None:
        if not all(text(v, 8192) for v in replacements.values()) or set(replacements) & set(clear):
            raise ValueError("Supply a nonempty replacement or clear, never both")
        with locked(self.path):
            values = self.read()
            values.update(replacements)
            for key in clear:
                values.pop(key, None)
            write_json(self.path, {"version": 1, "values": values})


class ProviderStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else directory()
        self.path = self.root / "provider-settings.json"
        self.secrets = SecretStore(self.root)

    def read(self) -> dict:
        data = (
            read_json(self.path) if self.path.exists() else {"version": 1, "profiles": [], "speech_auth": {}}
        )
        self.validate(data)
        return data

    @staticmethod
    def validate(data: object) -> None:
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "profiles", "speech_auth"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["profiles"], list)
            or len(data["profiles"]) > 32
            or not isinstance(data["speech_auth"], dict)
        ):
            raise ValueError("Invalid provider settings schema")
        profiles = {p["id"]: p for p in (validate_profile(p) for p in data["profiles"])}
        if len(profiles) != len(data["profiles"]):
            raise ValueError("Duplicate profile IDs")
        for backend, profile in data["speech_auth"].items():
            if (
                not isinstance(backend, str)
                or not isinstance(profile, str)
                or profiles.get(profile, {}).get("provider") != "openai"
            ):
                raise ValueError("Speech authentication requires an OpenAI profile")

    def view(self) -> dict:
        data = self.read()
        secrets = self.secrets.read()
        return {
            **data,
            "profiles": [
                {
                    **p,
                    "credential_configured": bool(
                        os.environ.get("OPENAI_API_KEY")
                        if p["auth"] == "environment"
                        else secrets.get("provider:" + p["id"])
                        if p["auth"] == "inline"
                        else False
                    ),
                }
                for p in data["profiles"]
            ],
            "capabilities": {
                "providers": ["ollama", "openai"],
                "speech_profile_adapters": ["openai"],
                "realtime_auth": "environment",
                "discovery": "models only; metadata, no inference",
                "openai_environment_configured": bool(os.environ.get("OPENAI_API_KEY")),
            },
        }

    def save(self, identity: str, body: object) -> dict:
        if not isinstance(body, dict) or body.keys() - {
            "name",
            "provider",
            "endpoint",
            "model",
            "auth",
            "credential",
            "clear_credential",
        }:
            raise ValueError("Unknown profile fields")
        profile = validate_profile(
            {"id": identity, **{k: v for k, v in body.items() if k not in {"credential", "clear_credential"}}}
        )
        clear = body.get("clear_credential", False)
        credential = body.get("credential")
        if type(clear) is not bool or ("credential" in body and (not text(credential, 8192) or clear)):
            raise ValueError("Supply a nonempty credential or explicit clear")
        if credential is not None and profile["auth"] != "inline":
            raise ValueError("Credential replacement requires inline authentication")
        # Serialize config and secrets under the config lock. Both files are atomic;
        # an interrupted multi-file change can leave an unused secret, never a leak.
        with locked(self.path):
            data = self.read()
            previous = next((p for p in data["profiles"] if p["id"] == identity), None)
            if previous and previous["provider"] != profile["provider"]:
                raise ValueError("Create a new profile to change provider type")
            data["profiles"] = [p for p in data["profiles"] if p["id"] != identity] + [profile]
            self.validate(data)
            self.secrets.change(
                {"provider:" + identity: credential} if credential is not None else {},
                ["provider:" + identity] if clear else [],
            )
            write_json(self.path, data)
        return self.view()

    def bind_speech(self, backend_id: str, profile_id: str | None) -> dict:
        from vauxr.speech.store import get_store

        backend = get_store().backends.get(backend_id)
        if backend is None or backend.adapter != "openai":
            raise ValueError("Shared authentication is supported for OpenAI Standard TTS only")
        if profile_id is not None and not isinstance(profile_id, str):
            raise ValueError("Choose a profile or null for environment authentication")

        def change(existing: dict) -> dict:
            source = existing or self.read()
            data = {**source, "speech_auth": dict(source["speech_auth"])}
            if profile_id is None:
                data["speech_auth"].pop(backend_id, None)
            else:
                data["speech_auth"][backend_id] = profile_id
            self.validate(data)
            return data

        update_json(self.path, change)
        return self.view()


def resolve_profile(profile_id: str, directory: Path | None = None) -> dict:
    """Internal adapter seam. Never serialize this result into an HTTP response."""
    store = ProviderStore(directory)
    profile = next((p for p in store.read()["profiles"] if p["id"] == profile_id), None)
    if profile is None:
        raise ValueError("Provider profile unavailable")
    key = (
        os.environ.get("OPENAI_API_KEY")
        if profile["auth"] == "environment"
        else store.secrets.read().get("provider:" + profile_id)
        if profile["auth"] == "inline"
        else None
    )
    if profile["provider"] == "openai" and not key:
        raise ValueError("Provider credential is not configured")
    return {
        "provider": profile["provider"],
        "endpoint": profile["endpoint"],
        "model": profile["model"],
        "api_key": key,
    }


def speech_credential(backend_id: str) -> str | None:
    store = ProviderStore()
    identity = store.read()["speech_auth"].get(backend_id)
    return resolve_profile(identity)["api_key"] if identity else os.environ.get("OPENAI_API_KEY")
