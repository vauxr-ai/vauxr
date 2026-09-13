"""Speech management API. Authorization is injected by the HTTP composition root.

Owner/scoped auth is injected here; device/offer input never sets selections.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiohttp import web

from speech import get_store, readiness

ManagementAuthorization = Callable[[web.Request], Awaitable[bool]]


def attach_speech_routes(app: web.Application, authorize: ManagementAuthorization) -> None:
    async def settings(request: web.Request) -> web.Response:
        if not await authorize(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        store = get_store()
        device_id = request.match_info.get("device_id")
        if request.method == "PATCH":
            try:
                store.update(await request.json(), device_id)
            except (ValueError, TypeError):
                return web.json_response({"error": "Invalid speech selection"}, status=400)
        result = store.view(device_id)
        states = await asyncio.gather(*(readiness(b) for b in store.backends.values()))
        for backend, state in zip(result["backends"], states, strict=True):
            backend["readiness"] = state
        return web.json_response(result)

    # The injected callback checks the explicit speech.configure policy before any I/O.
    settings.authz_boundary = True  # type: ignore[attr-defined]

    for path in ("/api/speech", "/api/devices/{device_id}/speech"):
        app.router.add_get(path, settings)
        app.router.add_patch(path, settings)
