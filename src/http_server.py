"""HTTP API server (`/api/...`) + portal static files.

Every protected handler resolves a principal and an explicit policy operation.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from aiohttp import web

import channel_registry
import device_registry as registry
import webhooks
from auth import authenticate
from auth_policy import HTTP_OPERATIONS, UNSHIPPED, Operation, Principal, Role, allowed, audit_denial
from config import get_config
from device_config import VALID_FOLLOW_UP_MODES, parse_button_actions
from enrollment_http import attach_enrollment
from lifecycle_http import attach_lifecycle
from owner_http import attach_owner, owner_middleware, session_principal
from protocol import encode_text_message

if TYPE_CHECKING:
    from channel_server import ChannelServer

log = logging.getLogger("vauxr.http")

WEB_CLIENT_DIST = "web-client/dist"

VALID_COMMANDS = frozenset({"set_volume", "mute", "unmute", "reboot", "ota", "set_barge_in"})

_FIRMWARE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.bin$")

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


Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def transport_boundary(handler: Handler) -> Handler:
    """Explicit boundary for handlers that authorize individual socket/media messages."""
    handler.authz_boundary = True  # type: ignore[attr-defined]
    return handler


@web.middleware
async def policy_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    if (request.method != "OPTIONS" and handler is not serve_static
            and not getattr(handler, "authz_boundary", False)):
        audit_denial(False)
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


def _http_principal(request: web.Request) -> Principal | None:
    principal = session_principal(request)
    if principal is None:
        header = request.headers.get("Authorization", "")
        principal = authenticate(header[7:] if header.startswith("Bearer ") else None)
        if principal is not None and principal.role == Role.OWNER:
            return None
    return principal


def _require_auth(handler: Handler) -> Handler:
    @wraps(handler)
    async def wrapped(request: web.Request) -> web.StreamResponse:
        principal = _http_principal(request)
        operation = HTTP_OPERATIONS.get(handler.__name__)
        if not allowed(principal, operation):
            audit_denial(principal is not None)
            status = 401 if principal is None else 403
            return web.json_response(
                {"error": "unauthorized" if status == 401 else "forbidden"}, status=status
            )
        if operation in UNSHIPPED:
            return web.json_response({"error": "operation not implemented"}, status=501)
        return await handler(request)

    return transport_boundary(wrapped)


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    if request.method == "OPTIONS":
        resp: web.StreamResponse = web.Response(status=204)
    else:
        resp = await handler(request)
    # Same-origin owner API never enables credentialed cross-origin access.
    if (not request.path.startswith(("/api/auth/", "/api/enrollment/", "/api/lifecycle/"))
            and "__Host-vauxr_owner" not in request.cookies):
        resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


def _device_dict(d) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": d.id,
        "name": d.name,
        "state": d.state,
        "lastSeen": d.last_seen.isoformat().replace("+00:00", "Z"),
        "config": {k: v for k, v in d.config.items()
                   if k in {"name", "voice", "follow_up_mode", "barge_in", "output_sample_rate"}},
    }
    if d.platform:
        out["platform"] = d.platform
    if d.fw_version:
        out["fw_version"] = d.fw_version
    return out


# --- /api/devices ---


@_require_auth
async def list_devices(_request: web.Request) -> web.Response:
    devices = [_device_dict(d) for d in registry.get_all()]
    log.info("200 devices listed: %d", len(devices))
    return web.json_response(devices)


@_require_auth
async def update_device(request: web.Request) -> web.Response:
    device_id = request.match_info["device_id"]
    device = registry.get(device_id)
    if device is None:
        return web.json_response({"error": "device not found"}, status=404)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)

    patch: dict[str, Any] = {}
    if "name" in body:
        if not isinstance(body["name"], str):
            return web.json_response({"error": "name must be a string"}, status=400)
        patch["name"] = body["name"]
    if "voice" in body:
        if not isinstance(body["voice"], bool):
            return web.json_response({"error": "voice must be a boolean"}, status=400)
        patch["voice"] = body["voice"]
    if "follow_up_mode" in body:
        mode = body["follow_up_mode"]
        if not isinstance(mode, str) or mode not in VALID_FOLLOW_UP_MODES:
            return web.json_response(
                {"error": "follow_up_mode must be 'auto' | 'always' | 'never'"}, status=400
            )
        patch["follow_up_mode"] = mode
    if "barge_in" in body:
        if not isinstance(body["barge_in"], bool):
            return web.json_response({"error": "barge_in must be a boolean"}, status=400)
        patch["barge_in"] = body["barge_in"]
    if "button_actions" in body:
        actions, err = parse_button_actions(body["button_actions"])
        if err:
            return web.json_response({"error": err}, status=400)
        patch["button_actions"] = actions

    nxt = registry.update_config(device_id, patch)  # type: ignore[arg-type]
    if nxt.get("name"):
        device.name = nxt["name"]
    log.info("200 device updated: %s", device_id)
    return web.json_response(_device_dict(device))


@_require_auth
async def announce(request: web.Request) -> web.Response:
    device_id = request.match_info["device_id"]
    device = registry.get(device_id)
    if device is None:
        return web.json_response({"error": "device not found"}, status=404)
    if device.state in ("listening", "processing"):
        return web.json_response({"error": "device busy"}, status=409)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"]:
        return web.json_response({"error": "missing text"}, status=400)

    text: str = body["text"]
    log.info("announce: synthesizing")
    from button_dispatch import announce_to_device

    if not await announce_to_device(device, text):
        return web.json_response({"error": "Selected speech provider unavailable"}, status=503)
    return web.json_response({"ok": True})


@_require_auth
async def device_command(request: web.Request) -> web.Response:
    device_id = request.match_info["device_id"]
    device = registry.get(device_id)
    if device is None:
        return web.json_response({"error": "device not found"}, status=404)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("command"), str):
        return web.json_response({"error": "missing command"}, status=400)
    cmd = body["command"]
    if cmd not in VALID_COMMANDS:
        return web.json_response({"error": f"unknown command: {cmd}"}, status=400)

    operation = Operation.FIRMWARE_INITIATE if cmd == "ota" else Operation.CONTROL
    if not allowed(_http_principal(request), operation):
        audit_denial(True)
        return web.json_response({"error": "forbidden"}, status=403)
    params = body.get("params")
    if cmd == "ota":
        if not isinstance(params, dict) or not isinstance(params.get("url"), str) or not params["url"]:
            return web.json_response({"error": "ota requires params.url"}, status=400)
        url = params["url"]
        if not url.startswith("http://") and not url.startswith("https://"):
            return web.json_response({"error": "ota url must be http:// or https://"}, status=400)
    if cmd == "set_barge_in":
        if not isinstance(params, dict) or "enabled" not in params:
            return web.json_response({"error": "set_barge_in requires params.enabled"}, status=400)
        enabled = params["enabled"]
        if not isinstance(enabled, bool):
            return web.json_response({"error": "params.enabled must be a boolean"}, status=400)
        nxt = registry.update_config(device_id, {"barge_in": enabled})
        log.info("command: set_barge_in enabled=%s → %s", enabled, device_id)
        return web.json_response({"ok": True, "barge_in": nxt.get("barge_in", enabled)})

    frame: dict[str, Any] = {"type": "device.control", "command": cmd}
    if "params" in body:
        frame["params"] = body["params"]

    await _device_send_text(device.ws, frame)
    log.info("command: %s → %s", cmd, device_id)
    return web.json_response({"ok": True})


# --- /api/channels ---


@_require_auth
async def list_channels(_request: web.Request) -> web.Response:
    channels = [
        {
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "active": c.active,
            "createdAt": c.createdAt,
            **({"builtin": c.builtin} if c.builtin is not None else {}),
        }
        for c in channel_registry.get_all()
    ]
    log.info("200 channels listed: %d", len(channels))
    return web.json_response(channels)


@_require_auth
async def create_channel(request: web.Request) -> web.Response:
    # Replaced by the independently scoped enrollment/lifecycle packages.
    return web.json_response({"error": "operation not implemented"}, status=501)


@_require_auth
async def delete_channel(request: web.Request) -> web.Response:
    # Replaced by the independently scoped enrollment/lifecycle packages.
    return web.json_response({"error": "operation not implemented"}, status=501)


@_require_auth
async def activate_channel(request: web.Request) -> web.Response:
    channel_id = request.match_info["channel_id"]
    if not channel_registry.activate(channel_id):
        return web.json_response({"error": "channel not found"}, status=404)
    return web.json_response({"ok": True})


@_require_auth
async def rotate_token(request: web.Request) -> web.Response:
    # Replaced by the independently scoped enrollment/lifecycle packages.
    return web.json_response({"error": "operation not implemented"}, status=501)


# --- WS shim (works with both aiohttp WebSocketResponse and the FakeWs used in tests) ---


async def _device_send_text(ws, obj: dict[str, Any]) -> None:
    if getattr(ws, "closed", False):
        return
    try:
        await ws.send_str(encode_text_message(obj))
    except ConnectionResetError:
        pass


# --- /api/webhooks ---


@_require_auth
async def list_webhooks(_request: web.Request) -> web.Response:
    return web.json_response([webhooks.public_dict(w) for w in webhooks.get_all()])


def _webhook_fields(body: dict[str, Any], *, require_name_url: bool) -> tuple[dict[str, Any], str | None]:
    """Pull name/url/authorization/body from a JSON body. Returns (fields, error)."""
    fields: dict[str, Any] = {}
    if "name" in body or require_name_url:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return {}, "name is required"
        fields["name"] = name.strip()
    if "url" in body or require_name_url:
        url = body.get("url")
        if not isinstance(url, str) or not url.strip():
            return {}, "url is required"
        err = webhooks.validate_url(url.strip())
        if err:
            return {}, err
        fields["url"] = url.strip()
    if "authorization" in body:
        auth = body.get("authorization")
        if auth is None:
            fields["authorization"] = ""
        elif not isinstance(auth, str):
            return {}, "authorization must be a string"
        else:
            fields["authorization"] = auth
    if "body" in body:
        parsed, err = webhooks.parse_body(body.get("body"))
        if err:
            return {}, err
        fields["body"] = parsed
    return fields, None


@_require_auth
async def create_webhook(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    fields, err = _webhook_fields(body, require_name_url=True)
    if err:
        return web.json_response({"error": err}, status=400)
    hook = webhooks.create(
        fields["name"],
        fields["url"],
        fields.get("authorization", ""),
        fields.get("body"),
    )
    log.info("201 webhook created: %s (%s)", hook.name, hook.id)
    return web.json_response(webhooks.public_dict(hook), status=201)


@_require_auth
async def update_webhook(request: web.Request) -> web.Response:
    webhook_id = request.match_info["webhook_id"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if webhooks.get(webhook_id) is None:
        return web.json_response({"error": "webhook not found"}, status=404)
    fields, err = _webhook_fields(body, require_name_url=False)
    if err:
        return web.json_response({"error": err}, status=400)
    kwargs: dict[str, Any] = {
        "name": fields.get("name"),
        "url": fields.get("url"),
        "authorization": fields.get("authorization"),
    }
    if "body" in fields:
        kwargs["body"] = fields["body"]
    hook = webhooks.update(webhook_id, **kwargs)
    if hook is None:
        return web.json_response({"error": "webhook not found"}, status=404)
    log.info("200 webhook updated: %s", webhook_id)
    return web.json_response(webhooks.public_dict(hook))


@_require_auth
async def delete_webhook(request: web.Request) -> web.Response:
    webhook_id = request.match_info["webhook_id"]
    if not webhooks.remove(webhook_id):
        return web.json_response({"error": "webhook not found"}, status=404)
    log.info("200 webhook deleted: %s", webhook_id)
    return web.json_response({"ok": True})


@_require_auth
async def duplicate_webhook(request: web.Request) -> web.Response:
    webhook_id = request.match_info["webhook_id"]
    hook = webhooks.duplicate(webhook_id)
    if hook is None:
        return web.json_response({"error": "webhook not found"}, status=404)
    log.info("201 webhook duplicated: %s (%s)", hook.name, hook.id)
    return web.json_response(webhooks.public_dict(hook), status=201)


# --- Static files ---


async def serve_static(request: web.Request) -> web.StreamResponse:
    if request.path == "/api" or request.path.startswith("/api/"):
        return web.json_response({"error": "not found"}, status=404)
    rel = request.path.lstrip("/")
    base = os.path.join(os.getcwd(), WEB_CLIENT_DIST)
    if not os.path.isdir(base):
        return web.json_response({"error": "not found"}, status=404)

    candidate = os.path.normpath(os.path.join(base, rel))
    if os.path.commonpath((os.path.realpath(base), os.path.realpath(candidate))) != os.path.realpath(base):
        return web.json_response({"error": "not found"}, status=404)

    if not os.path.exists(candidate) or os.path.isdir(candidate):
        candidate = os.path.join(base, "index.html")
    if not os.path.exists(candidate):
        return web.json_response({"error": "not found"}, status=404)

    ext = os.path.splitext(candidate)[1]
    ctype = _MIME_TYPES.get(ext) or mimetypes.guess_type(candidate)[0] or "application/octet-stream"
    return web.FileResponse(candidate, headers={"Content-Type": ctype})


@_require_auth
async def serve_firmware(request: web.Request) -> web.StreamResponse:
    """Authenticated firmware download; publication is a separate owner operation."""
    name = request.match_info["filename"]
    if not _FIRMWARE_NAME.fullmatch(name):
        return web.json_response({"error": "not found"}, status=404)
    root = os.path.realpath(os.path.join(get_config().data_dir, "firmware"))
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root or not os.path.isfile(path):
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(
        path,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": f'attachment; filename="{name}"',
        },
    )


async def _authorize_speech_management(request: web.Request) -> bool:
    """Legacy management boundary; replace with owner/scoped policy during #45/#49 integration."""
    return await _bearer_token_valid(request)


def attach_http_routes(app: web.Application) -> None:
    from speech_http import attach_speech_routes

    attach_speech_routes(app, _authorize_speech_management)
    attach_owner(app)
    attach_enrollment(app)
    attach_lifecycle(app)
    async def _options(_r: web.Request) -> web.Response:
        return web.Response(status=204)

    app.router.add_route("OPTIONS", "/{tail:.*}", _options)
    app.router.add_get("/api/devices", list_devices)
    app.router.add_patch("/api/devices/{device_id}", update_device)
    app.router.add_post("/api/devices/{device_id}/announce", announce)
    app.router.add_post("/api/devices/{device_id}/command", device_command)
    app.router.add_get("/api/channels", list_channels)
    app.router.add_post("/api/channels", create_channel)
    app.router.add_delete("/api/channels/{channel_id}", delete_channel)
    app.router.add_post("/api/channels/{channel_id}/activate", activate_channel)
    app.router.add_post("/api/channels/{channel_id}/rotate", rotate_token)
    app.router.add_get("/api/webhooks", list_webhooks)
    app.router.add_post("/api/webhooks", create_webhook)
    app.router.add_patch("/api/webhooks/{webhook_id}", update_webhook)
    app.router.add_delete("/api/webhooks/{webhook_id}", delete_webhook)
    app.router.add_post("/api/webhooks/{webhook_id}/duplicate", duplicate_webhook)
    app.router.add_get("/firmware/{filename}", serve_firmware)


def make_http_app(_channel_server: ChannelServer | None = None) -> web.Application:
    app = web.Application(middlewares=[owner_middleware, cors_middleware, policy_middleware])
    attach_http_routes(app)
    app.router.add_get("/{tail:.*}", serve_static)
    return app
