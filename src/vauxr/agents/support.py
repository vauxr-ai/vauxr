"""Built-in Support, independent of integration sockets and credentials."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from vauxr.config import get_config
from vauxr.config_files import read_json, update_json
from vauxr.web.owner import session_principal

EMPTY = {"provider": "", "endpoint": "", "model": ""}


def settings() -> dict[str, str]:
    path = Path(get_config().data_dir) / "support.json"
    return validate(read_json(path)) if path.exists() else dict(EMPTY)


def runtime_view(config: dict[str, str]) -> dict[str, str]:
    """Public runtime configuration; resolved credentials never cross this boundary."""
    if "profile_id" not in config:
        return {**config, "status": "configured" if config != EMPTY else "setup_needed"}
    from vauxr.settings.providers import resolve_profile

    try:
        resolved = resolve_profile(config["profile_id"])
    except ValueError:
        return {**config, "status": "setup_needed"}
    return {**config, "status": "configured" if resolved["provider"] == "ollama" else "unsupported"}


def validate(body: object) -> dict[str, str]:
    if isinstance(body, dict) and set(body) == {"profile_id"}:
        from vauxr.settings.providers import ID, ProviderStore

        identity = body["profile_id"]
        if not isinstance(identity, str) or not ID.fullmatch(identity):
            raise ValueError("Invalid profile reference")
        if not any(p["id"] == identity for p in ProviderStore().read()["profiles"]):
            raise ValueError("Provider profile unavailable")
        return {"profile_id": identity}
    if (
        not isinstance(body, dict)
        or set(body) != set(EMPTY)
        or any(not isinstance(v, str) for v in body.values())
    ):
        raise ValueError("invalid local runtime settings")
    if body == EMPTY:
        return dict(EMPTY)
    if body["provider"] != "ollama" or not 1 <= len(body["model"].strip()) <= 120:
        raise ValueError("Choose a local runtime and model")
    endpoint = urlsplit(body["endpoint"])
    try:
        address = ipaddress.ip_address(endpoint.hostname or "")
        loopback = address.is_loopback or address.is_private
    except ValueError:
        loopback = bool(endpoint.hostname and endpoint.hostname.endswith((".local", ".lan")))
    if (
        endpoint.scheme != "http"
        or not loopback
        or endpoint.username
        or endpoint.password
        or endpoint.path not in ("", "/")
        or endpoint.query
        or endpoint.fragment
        or not endpoint.port
        or "cloud" in body["model"].lower()
        or any(ord(c) < 32 for c in body["model"])
    ):
        raise ValueError("Use an explicit local HTTP host and port and a local model")
    return {"provider": "ollama", "endpoint": body["endpoint"].rstrip("/"), "model": body["model"].strip()}


async def answer(device_id: str, text: str) -> str:
    """Compatibility entry point; Support runs the same registered contract."""
    from vauxr.agents import registry
    from vauxr.agents.langchain import answer as registered_answer

    registry.ensure_support()
    return await registered_answer("vauxr-support", device_id, text)


def attach_support(
    app: web.Application,
    boundary: Callable[
        [Callable[[web.Request], Awaitable[web.StreamResponse]]],
        Callable[[web.Request], Awaitable[web.StreamResponse]],
    ],
) -> None:
    async def handle(request: web.Request) -> web.Response:
        if session_principal(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            if request.query_string:
                raise ValueError()
            if request.method == "POST":
                async with asyncio.timeout(10):
                    raw = await request.content.read(4097)
                if len(raw) > 4096 or request.content_type != "application/json":
                    raise ValueError()
                import json

                from vauxr.config_files import unique_object

                config = validate(json.loads(raw, object_pairs_hook=unique_object))
                update_json(Path(get_config().data_dir) / "support.json", lambda _: config)
            config = settings()
            return web.json_response(
                runtime_view(config),
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, TypeError, TimeoutError):
            return web.json_response(
                {
                    "error": "Use a local Ollama runtime with an explicit local address, port and installed local model"
                },
                status=400,
            )
        except OSError:
            return web.json_response({"error": "Save unavailable; refresh before retrying"}, status=503)

    app.router.add_get("/api/support", boundary(handle))
    app.router.add_post("/api/support", boundary(handle))
