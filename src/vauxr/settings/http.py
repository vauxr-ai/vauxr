"""Settings routes attached through the existing speech feature boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict

import aiohttp
from aiohttp import web

from vauxr.config_files import update_json
from vauxr.settings.providers import ID, ProviderStore, resolve_profile
from vauxr.speech.catalog import parse_backends
from vauxr.speech.store import SpeechStore, VoiceConflict, get_store


def speech_registry() -> dict:
    return {"version": 1, "providers": [asdict(b) for b in get_store().backends.values()]}


def save_backend(identity: str, body: object) -> dict:
    store = get_store()
    if not isinstance(body, dict) or body.get("id") != identity:
        raise ValueError("Deployment ID must match the request")
    previous = store.backends.get(identity)
    if previous is not None and (
        body.get("kind") != previous.kind or body.get("adapter") != previous.adapter
    ):
        raise ValueError("Edit an existing deployment without changing its ID, mode or adapter")
    if previous is None and (not isinstance(identity, str) or not ID.fullmatch(identity)):
        raise ValueError("Invalid deployment ID")
    parsed = parse_backends([body], store.path)[0]
    if (
        len(parsed.host) > 253
        or any(c in parsed.host for c in " /@?#\\")
        or any(ord(c) < 33 for c in parsed.host)
        or len(parsed.model) > 200
        or any(ord(c) < 32 for c in parsed.model)
        or any(len(v) > 200 or any(ord(c) < 32 for c in v) for v in parsed.voices)
    ):
        raise ValueError("Invalid deployment host, model or voice")
    # The OpenAI adapter transmits credentials only to its fixed verified origin.
    if parsed.adapter == "openai" and (parsed.host != "api.openai.com" or parsed.port != 443):
        raise ValueError("OpenAI credentials require api.openai.com:443")

    def change(data: dict) -> dict:
        result = {**data, "providers": [body if b["id"] == identity else b for b in data["providers"]]}
        if previous is None:
            result["providers"].append(body)
        candidate = object.__new__(SpeechStore)
        candidate.path = store.path
        candidate._load(json.loads(json.dumps(result)))
        from vauxr.devices import registry

        affected = set(store.devices) | set(store.assignments) | set(store.capabilities)
        affected |= {device.id for device in registry.get_all()}
        for device_id in affected:
            conflict = store._active_change(candidate, device_id)
            if conflict:
                raise VoiceConflict(conflict)
        return result

    store._load(update_json(store.path, change))
    return speech_registry()


async def discover_profile(identity: str) -> dict:
    resolved = resolve_profile(identity)
    suffix = "/api/tags" if resolved["provider"] == "ollama" else "/v1/models"
    headers = {"Authorization": "Bearer " + resolved["api_key"]} if resolved["api_key"] else {}
    try:
        async with (
            asyncio.timeout(5),
            aiohttp.ClientSession(headers=headers) as client,
            client.get(resolved["endpoint"].rstrip("/") + suffix, allow_redirects=False) as response,
        ):
            if response.status != 200:
                raise ValueError("Model discovery unavailable; enter the model ID manually")
            raw = await response.content.read(262145)
            if len(raw) > 262144:
                raise ValueError("Model discovery response too large")
            data = json.loads(raw)
            rows = data.get("models" if resolved["provider"] == "ollama" else "data", [])
            key = "name" if resolved["provider"] == "ollama" else "id"
            models = sorted(
                {
                    r[key]
                    for r in rows
                    if isinstance(r, dict)
                    and isinstance(r.get(key), str)
                    and 0 < len(r[key]) <= 200
                    and not any(ord(c) < 32 for c in r[key])
                }
            )[:256]
            return {"models": models, "source": "ollama-tags" if suffix == "/api/tags" else "openai-models"}
    except (aiohttp.ClientError, OSError, TimeoutError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError("Model discovery unavailable; enter the model ID manually") from None


async def discover_speech(identity: str) -> dict:
    """Wyoming describe metadata; never synthesizes or starts voice."""
    from vauxr.speech.wyoming_protocol import WyomingEvent, encode_event, parse_wyoming_events

    backend = get_store().backends.get(identity)
    if backend is None:
        raise ValueError("Deployment unavailable")
    if backend.adapter in ("openai", "openai-live"):
        return {
            "models": [],
            "voices": list(backend.voices),
            "source": "configured",
            "message": "No built-in voice discovery endpoint; configured voice IDs are retained. Enter model IDs manually.",
        }
    writer = None
    try:
        async with asyncio.timeout(3):
            reader, writer = await asyncio.open_connection(backend.host, backend.port)
            writer.write(encode_event(WyomingEvent("describe")))
            await writer.drain()
            buffer = b""
            received = 0
            while received < 262144:
                chunk = await reader.read(8192)
                if not chunk:
                    break
                received += len(chunk)
                events, buffer = parse_wyoming_events(buffer + chunk)
                for event in events:
                    if event.type == "info":
                        programs = event.data.get("asr" if backend.kind == "stt" else "tts", [])
                        models, voices = set(), set()
                        for program in programs:
                            for model in program.get("models", []):
                                if isinstance(model.get("name"), str):
                                    models.add(model["name"])
                            for voice in program.get("voices", []):
                                if isinstance(voice.get("name"), str):
                                    voices.add(voice["name"])
                        return {
                            "models": sorted(models)[:256],
                            "voices": sorted(voices)[:256],
                            "source": "wyoming-describe",
                        }
    except (OSError, TimeoutError, ValueError, TypeError, AttributeError):
        pass
    finally:
        if writer is not None:
            writer.close()
    raise ValueError("Discovery unavailable; retained configuration can be edited manually")


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def attach_settings_routes(
    app: web.Application,
    authorize: Callable[[web.Request], Awaitable[bool]],
    boundary: Callable[[Handler], Handler],
) -> None:
    async def handle(request: web.Request) -> web.Response:
        if not await authorize(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            if request.query_string:
                raise ValueError("Query parameters are unsupported")
            path = request.path
            identity = request.match_info.get("profile_id", "")
            if path == "/api/settings/providers":
                result = ProviderStore().view()
            elif path.endswith("/speech-auth"):
                body = await request.json()
                if not isinstance(body, dict) or set(body) != {"backend_id", "profile_id"}:
                    raise ValueError("Expected backend_id and profile_id")
                result = ProviderStore().bind_speech(body["backend_id"], body["profile_id"])
            elif identity:
                result = (
                    await discover_profile(identity)
                    if path.endswith("/discover")
                    else ProviderStore().save(identity, await request.json())
                )
            elif path == "/api/settings/speech":
                result = speech_registry()
            elif path.endswith("/discover"):
                result = await discover_speech(request.match_info["backend_id"])
            else:
                result = save_backend(request.match_info["backend_id"], await request.json())
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except VoiceConflict:
            return web.json_response(
                {"error": "Voice is active. Stop voice before changing its deployment."}, status=409
            )
        except (ValueError, TypeError, KeyError):
            # Never reflect provider bodies, identifiers, submitted values, or secrets.
            return web.json_response(
                {"error": "Invalid or unavailable settings. Check the fields and refresh before retrying."},
                status=400,
            )
        except OSError:
            return web.json_response(
                {"error": "Settings storage unavailable. Refresh before retrying."}, status=503
            )

    wrapped = boundary(handle)
    app.router.add_get("/api/settings/providers", wrapped)
    app.router.add_put("/api/settings/providers/profiles/{profile_id}", wrapped)
    app.router.add_post("/api/settings/providers/profiles/{profile_id}/discover", wrapped)
    app.router.add_put("/api/settings/providers/speech-auth", wrapped)
    app.router.add_get("/api/settings/speech", wrapped)
    app.router.add_put("/api/settings/speech/{backend_id}", wrapped)
    app.router.add_post("/api/settings/speech/{backend_id}/discover", wrapped)
