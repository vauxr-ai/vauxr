"""Speech management API. Authorization is injected by the HTTP composition root.

The composition root enforces owner/scoped auth; device/offer input never sets selections.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiohttp import web

from vauxr.speech.store import VoiceConflict, get_store, readiness

ManagementAuthorization = Callable[[web.Request], Awaitable[bool]]


SpeechHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def attach_speech_routes(
    app: web.Application,
    authorize: ManagementAuthorization,
    boundary: Callable[[SpeechHandler], SpeechHandler],
) -> None:
    from vauxr.settings.http import attach_settings_routes

    attach_settings_routes(app, authorize, boundary)

    async def settings(request: web.Request) -> web.Response:
        if not await authorize(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        store = get_store()
        device_id = request.match_info.get("device_id")
        agent_id = request.match_info.get("agent_id")
        if agent_id:
            from vauxr.agents import registry

            if registry.get_by_id(agent_id) is None:
                return web.json_response({"error": "Agent unavailable; refresh agents"}, status=404)
        if request.method == "PATCH":
            try:
                store.update(await request.json(), device_id, agent_id)
            except VoiceConflict as error:
                return web.json_response({"error": str(error)}, status=409)
            except (ValueError, TypeError) as error:
                return web.json_response({"error": str(error)}, status=400)
        result = store.view(device_id, agent_id)
        states = (["disabled (chat-only)"] * len(store.backends) if result["policy"]["chat_only"]
                  else await asyncio.gather(*(readiness(b) for b in store.backends.values())))
        for backend, state in zip(result["backends"], states, strict=True):
            backend["readiness"] = state
        return web.json_response(result)

    settings = boundary(settings)
    for path in ("/api/speech", "/api/devices/{device_id}/speech", "/api/agents/{agent_id}/speech"):
        app.router.add_get(path, settings)
        app.router.add_patch(path, settings)
