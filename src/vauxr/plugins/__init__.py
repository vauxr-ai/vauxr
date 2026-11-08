"""Distribution of implemented Vauxr integration plugins."""
from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from importlib.resources import files

from aiohttp import web

from vauxr.web.owner import session_principal

REVISION = "9b3b0f5fca46b536aa69d258c2ec161ad2698b2a"
PACKAGE = "openclaw-9b3b0f5f.tgz"


def package() -> bytes:
    return files(__package__).joinpath(PACKAGE).read_bytes()


def catalog() -> list[dict[str, str]]:
    return [{"id": "support", "name": "Vauxr Support", "distribution": "builtin"}, {
        "id": "openclaw", "name": "OpenClaw", "distribution": "archive", "revision": REVISION,
        "source": "https://github.com/vauxr-ai/vauxr-openclaw/tree/" + REVISION,
        "sha256": hashlib.sha256(package()).hexdigest(), "download": "/api/plugins/openclaw/package",
        "filename": "vauxr-openclaw-9b3b0f5f.tgz",
    }, {"id":"langchain", "name":"LangChain", "distribution":"bundled"}]


def attach_plugins(
    app: web.Application,
    boundary: Callable[[Callable[[web.Request], Awaitable[web.StreamResponse]]], Callable[[web.Request], Awaitable[web.StreamResponse]]],
) -> None:
    async def handle(request: web.Request) -> web.Response:
        if session_principal(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        if request.query_string:
            return web.json_response({"error": "invalid request"}, status=400)
        if request.path.endswith("/package"):
            return web.Response(body=package(), content_type="application/gzip", headers={
                "Content-Disposition": 'attachment; filename="vauxr-openclaw-9b3b0f5f.tgz"',
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            })
        return web.json_response(catalog(), headers={"Cache-Control": "no-store"})

    app.router.add_get("/api/plugins", boundary(handle))
    app.router.add_get("/api/plugins/openclaw/package", boundary(handle))
