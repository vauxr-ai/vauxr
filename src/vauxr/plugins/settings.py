"""Plugin-declared settings contract, independent of package distribution.

A loaded plugin explicitly calls register(PluginSettings(...)). Merely appearing
in the downloadable package catalog does not register server settings. Consumers
use PluginStore.values(id) internally; HTTP uses view(), which masks secrets.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from aiohttp import web

from vauxr.config_files import locked, read_json, write_json
from vauxr.settings.providers import ID, SecretStore, directory, text


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    type: str
    required: bool = False
    default: str | int | bool | None = None
    choices: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None


@dataclass(frozen=True)
class PluginSettings:
    id: str
    name: str
    fields: tuple[SettingField, ...]


_registered: dict[str, PluginSettings] = {}


def validate_value(field: SettingField, value: object) -> None:
    valid = (
        type(value) is bool
        if field.type == "boolean"
        else type(value) is int
        if field.type == "integer"
        else isinstance(value, str) and len(value) <= 4096 and not any(ord(c) < 32 for c in value)
    )
    if not valid or (field.required and value == ""):
        raise ValueError("Invalid declared setting type or required value")
    if field.choices and value not in field.choices:
        raise ValueError("Choose a declared setting option")
    if field.type == "integer" and (
        (field.min is not None and value < field.min) or (field.max is not None and value > field.max)
    ):
        raise ValueError("Setting outside declared bounds")


def register(schema: PluginSettings) -> None:
    if (
        not ID.fullmatch(schema.id)
        or not text(schema.name)
        or schema.id in _registered
        or not isinstance(schema.fields, tuple)
        or not 1 <= len(schema.fields) <= 32
    ):
        raise ValueError("Invalid or duplicate plugin settings registration")
    seen = set()
    for field in schema.fields:
        if (
            not ID.fullmatch(field.key)
            or field.key in seen
            or not text(field.label)
            or field.type not in ("string", "integer", "boolean", "secret")
            or type(field.required) is not bool
            or not isinstance(field.choices, tuple)
            or len(field.choices) > 64
            or len(set(field.choices)) != len(field.choices)
            or any(not text(choice) for choice in field.choices)
            or (field.choices and field.type != "string")
            or (field.type == "secret" and field.default is not None)
            or any(type(bound) is not int for bound in (field.min, field.max) if bound is not None)
            or (field.type != "integer" and (field.min is not None or field.max is not None))
            or (field.min is not None and field.max is not None and field.min > field.max)
        ):
            raise ValueError("Invalid plugin field declaration")
        if field.default is not None:
            validate_value(field, field.default)
        seen.add(field.key)
    _registered[schema.id] = schema


class PluginStore:
    def __init__(self, root: Path | None = None) -> None:
        root = root if root is not None else directory()
        self.path = root / "plugin-settings.json"
        self.secrets = SecretStore(root)

    def read(self) -> dict:
        data = read_json(self.path) if self.path.exists() else {"version": 1, "plugins": {}}
        if (
            not isinstance(data, dict)
            or set(data) != {"version", "plugins"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or not isinstance(data["plugins"], dict)
        ):
            raise ValueError("Invalid plugin settings schema")
        # Unloaded plugins retain their JSON, but cannot be read/written through API.
        for identity, values in data["plugins"].items():
            if not isinstance(identity, str) or not isinstance(values, dict):
                raise TypeError("Invalid plugin settings record")
            if identity in _registered:
                fields = {f.key: f for f in _registered[identity].fields if f.type != "secret"}
                for key, value in values.items():
                    if key not in fields:
                        raise ValueError("Undeclared persisted plugin field")
                    validate_value(fields[key], value)
        return data

    def values(self, identity: str) -> dict:
        schema = _registered.get(identity)
        if schema is None:
            raise ValueError("Plugin settings unavailable")
        result = {f.key: f.default for f in schema.fields if f.default is not None}
        result.update(self.read()["plugins"].get(identity, {}))
        secrets = self.secrets.read()
        for field in schema.fields:
            if field.type == "secret" and (value := secrets.get(f"plugin:{identity}:{field.key}")):
                result[field.key] = value
        return result

    def view(self) -> dict:
        rows = []
        for schema in _registered.values():
            values = self.values(schema.id)
            rows.append(
                {
                    "id": schema.id,
                    "name": schema.name,
                    "fields": [asdict(f) for f in schema.fields],
                    "values": {
                        f.key: values[f.key] for f in schema.fields if f.type != "secret" and f.key in values
                    },
                    "secrets": {f.key: bool(values.get(f.key)) for f in schema.fields if f.type == "secret"},
                }
            )
        return {"plugins": rows}

    def save(self, identity: str, body: object) -> dict:
        schema = _registered.get(identity)
        if schema is None:
            raise ValueError("Plugin settings unavailable")
        if not isinstance(body, dict) or body.keys() - {"values", "secrets", "clear_secrets"}:
            raise ValueError("Unknown plugin settings fields")
        values, secrets, clear = (
            body.get("values", {}),
            body.get("secrets", {}),
            body.get("clear_secrets", []),
        )
        if (
            not isinstance(values, dict)
            or not isinstance(secrets, dict)
            or not isinstance(clear, list)
            or any(not isinstance(k, str) for k in clear)
            or len(set(clear)) != len(clear)
            or set(secrets) & set(clear)
            or set(values) & set(secrets)
        ):
            raise ValueError("Invalid plugin settings update")
        fields = {f.key: f for f in schema.fields}
        for key, value in {**values, **secrets}.items():
            if key not in fields or (fields[key].type == "secret") != (key in secrets):
                raise ValueError("Undeclared or misclassified plugin setting")
            validate_value(fields[key], value)
            if key in secrets and not text(value, 4096):
                raise ValueError("Secret replacement must be nonempty")
        if any(key not in fields or fields[key].type != "secret" for key in clear):
            raise ValueError("Only declared secrets may be cleared")
        with locked(self.path):
            data = self.read()
            effective = {**self.values(identity), **values, **secrets}
            for key in clear:
                effective.pop(key, None)
            for field in schema.fields:
                if field.required and (field.key not in effective or effective[field.key] == ""):
                    raise ValueError("Required setting unavailable")
            self.secrets.change(
                {f"plugin:{identity}:{k}": v for k, v in secrets.items()},
                [f"plugin:{identity}:{k}" for k in clear],
            )
            data["plugins"][identity] = {**data["plugins"].get(identity, {}), **values}
            write_json(self.path, data)
        return self.view()


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def attach_plugin_settings(app: web.Application, boundary: Callable[[Handler], Handler]) -> None:
    from vauxr.web.owner import session_principal

    async def handle(request: web.Request) -> web.Response:
        if session_principal(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            if request.query_string:
                raise ValueError
            store = PluginStore()
            result = (
                store.view()
                if request.method == "GET"
                else store.save(request.match_info["plugin_id"], await request.json())
            )
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (ValueError, TypeError, KeyError):
            return web.json_response(
                {"error": "Invalid plugin settings. Check declared fields and refresh before retrying."},
                status=400,
            )
        except OSError:
            return web.json_response({"error": "Settings storage unavailable."}, status=503)

    app.router.add_get("/api/settings/plugins", boundary(handle))
    app.router.add_put("/api/settings/plugins/{plugin_id}", boundary(handle))
