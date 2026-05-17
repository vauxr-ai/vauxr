"""HTTP API server (`/api/...`) + portal static files.

Phase 6 introduces the skeleton with `GET /api/devices`, the shared bearer
auth check, and CORS handling. Announce / command / channels endpoints land
in Phases 12–13.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from typing import TYPE_CHECKING

from aiohttp import web

from .auth import validate_channel_http_token, validate_token
from .config import get_config
from . import device_registry as registry

if TYPE_CHECKING:
    from .channel_server import ChannelServer

log = logging.getLogger("vauxr.http")


# Static files for the React portal — same layout as the Node version
# (web-client/dist served from cwd).
WEB_CLIENT_DIST = "web-client/dist"


_MIME_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


async def _bearer_token_valid(request: web.Request) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    token = header[len("Bearer ") :]
    if validate_token(token).ok:
        return True
    return (await validate_channel_http_token(token)).ok


def _require_auth(handler):
    """Decorator: 401 unless the request carries a valid bearer token."""

    async def wrapped(request: web.Request) -> web.StreamResponse:
        if not await _bearer_token_valid(request):
            log.info("401 unauthorized %s %s", request.method, request.path)
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    return wrapped


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=204)
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


@_require_auth
async def list_devices(_request: web.Request) -> web.Response:
    devices = [
        {
            "id": d.id,
            "name": d.name,
            "state": d.state,
            "lastSeen": d.last_seen.isoformat().replace("+00:00", "Z"),
            "config": dict(d.config),
        }
        for d in registry.get_all()
    ]
    log.info("200 devices listed: %d", len(devices))
    return web.json_response(devices)


async def serve_static(request: web.Request) -> web.StreamResponse:
    """SPA-style static file server: unknown paths fall back to index.html."""
    rel = request.path.lstrip("/")
    base = os.path.join(os.getcwd(), WEB_CLIENT_DIST)
    if not os.path.isdir(base):
        return web.json_response({"error": "not found"}, status=404)

    candidate = os.path.normpath(os.path.join(base, rel))
    if not candidate.startswith(base):
        # Path traversal — refuse.
        return web.json_response({"error": "not found"}, status=404)

    if not os.path.exists(candidate) or os.path.isdir(candidate):
        candidate = os.path.join(base, "index.html")

    if not os.path.exists(candidate):
        return web.json_response({"error": "not found"}, status=404)

    ext = os.path.splitext(candidate)[1]
    content_type = _MIME_TYPES.get(ext) or mimetypes.guess_type(candidate)[0] or "application/octet-stream"
    return web.FileResponse(candidate, headers={"Content-Type": content_type})


def attach_http_routes(app: web.Application) -> None:
    """Attach API routes + a catch-all for static files.

    Static files are handled last so API routes take priority on /api/*.
    """
    async def _options(_r: web.Request) -> web.Response:
        return web.Response(status=204)

    app.router.add_route("OPTIONS", "/{tail:.*}", _options)
    app.router.add_get("/api/devices", list_devices)
    # Static fallback (registered last so /api/* matches first).
    # NOTE: WS routes are added separately by server.make_app.


def make_http_app(_channel_server: "ChannelServer | None" = None) -> web.Application:
    """Build an aiohttp app exposing only the HTTP API (used by tests)."""
    app = web.Application(middlewares=[cors_middleware])
    attach_http_routes(app)
    app.router.add_get("/{tail:.*}", serve_static)
    return app


def start_http_server(channel_server: "ChannelServer | None" = None) -> web.AppRunner:
    """Convenience runner — currently unused by the unified server in server.py.

    Kept to mirror `startHttpServer` from src/http-server.ts. Phase 13 will
    wire the real combined app together; this exists so phase-6 tests can run
    against an HTTP-only app.
    """
    raise NotImplementedError("Use make_http_app() in tests; see server.main for production")
