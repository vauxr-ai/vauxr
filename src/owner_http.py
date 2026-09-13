"""Configured HTTPS boundary for owner credentials and cookie-authenticated APIs."""

import asyncio
import hmac
import ipaddress
import json
import os
from collections.abc import Awaitable, Callable

from aiohttp import web

from auth import get_store
from auth_policy import Principal
from owner_auth import SESSION_SECONDS, OwnerAuth, OwnerError, environment_token, trusted_origin

OWNER = web.AppKey("owner_auth", OwnerAuth)
ORIGIN = web.AppKey("owner_origin", str)
PROXIES = web.AppKey("owner_proxies", tuple)
COOKIE = "__Host-vauxr_owner"
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def secure_request(request: web.Request) -> bool:
    origin = request.app[ORIGIN]
    if not origin or request.headers.get("Host") != origin.removeprefix("https://"):
        return False
    # Never accept arbitrary forwarding chains or Forwarded in addition to our contract.
    if "Forwarded" in request.headers:
        return False
    forwarded = request.headers.getall("X-Forwarded-Proto", [])
    if request.secure:
        return not forwarded
    peer = request.transport.get_extra_info("peername") if request.transport else None
    if not peer or forwarded != ["https"]:
        return False
    try:
        address = ipaddress.ip_address(peer[0])
        return any(address in network for network in request.app[PROXIES])
    except ValueError:
        return False


def session_principal(request: web.Request) -> Principal | None:
    if OWNER not in request.app:
        return None
    result = request.app[OWNER].session(request.cookies.get(COOKIE, ""))
    return result[0] if result else None


@web.middleware
async def owner_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    owner_path = request.path.startswith("/api/auth/")
    cookie = request.cookies.get(COOKIE, "")
    if not owner_path and not cookie:
        return await handler(request)
    try:
        if not secure_request(request):
            raise web.HTTPForbidden()
        origin = request.headers.get("Origin")
        if origin is not None and origin != request.app[ORIGIN]:
            raise web.HTTPForbidden()
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if origin != request.app[ORIGIN]:
                raise web.HTTPForbidden()
            if not owner_path or request.path.endswith("/logout"):
                result = request.app[OWNER].session(cookie)
                csrf = request.headers.get("X-CSRF-Token", "")
                if result is None or not hmac.compare_digest(
                        csrf.encode("utf-8"), result[1].csrf.encode("utf-8")):
                    raise web.HTTPForbidden()
        response = await handler(request)
    except web.HTTPException as exc:
        response = web.json_response({"error": "forbidden" if exc.status == 403 else "invalid_request"},
                                     status=exc.status)
    except (OSError, ValueError):
        response = web.json_response({"error": "owner_unavailable"}, status=503)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def owner_endpoint(request: web.Request) -> web.Response:
    service = request.app[OWNER]
    action = request.match_info["action"]
    if request.query_string:
        return web.json_response({"error": "invalid_request"}, status=400)
    if request.method in {"GET", "HEAD"}:
        if action == "status":
            return web.json_response(service.status())
        if action == "session":
            result = service.session(request.cookies.get(COOKIE, ""))
            if result:
                return web.json_response({"version": 1, "csrf_token": result[1].csrf,
                                          "expires_at": result[1].expires})
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"error": "not_found"}, status=404)
    try:
        service.rate_limit()
        if request.content_type != "application/json":
            raise OwnerError("invalid_request")
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.content.iter_chunked(1024):
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise OwnerError("invalid_request")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise OwnerError("invalid_request")
        if action == "claim" and set(body) == {"code"}:
            return web.json_response(service.claim(body["code"]))
        if action == "save" and set(body) == {"save_acknowledgement", "saved"}:
            service.acknowledge(body["save_acknowledgement"], body["saved"])
            return web.json_response({"version": 1, "state": "generated"})
        if action == "login" and set(body) == {"operator_token"}:
            cookie, session = service.login(body["operator_token"])
            response = web.json_response({"version": 1, "csrf_token": session.csrf,
                                          "expires_at": session.expires})
            response.set_cookie(COOKIE, cookie, secure=True, httponly=True, samesite="Strict",
                                path="/", max_age=SESSION_SECONDS)
            return response
        if action == "logout" and not body:
            service.logout(request.cookies.get(COOKIE, ""))
            response = web.json_response({"version": 1, "logged_out": True})
            response.del_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
            return response
        raise OwnerError("invalid_request")
    except OwnerError as exc:
        code = str(exc)
        return web.json_response({"error": code}, status=429 if code == "rate_limited" else 400)
    except (json.JSONDecodeError, UnicodeError, TimeoutError):
        return web.json_response({"error": "invalid_request"}, status=400)


owner_endpoint.authz_boundary = True  # type: ignore[attr-defined]


def attach_owner(app: web.Application) -> None:
    if owner_middleware not in app.middlewares:
        app.middlewares.insert(0, owner_middleware)
    raw_origin = os.environ.get("OWNER_HTTPS_ORIGIN", "")
    app[ORIGIN] = trusted_origin(raw_origin) if raw_origin else ""
    raw_proxies = os.environ.get("OWNER_TRUSTED_PROXIES", "")
    app[PROXIES] = tuple(ipaddress.ip_network(value.strip()) for value in raw_proxies.split(",") if value)
    app[OWNER] = OwnerAuth(get_store(), environment_token())

    async def startup(application: web.Application) -> None:
        application[OWNER].initialize()

    app.on_startup.append(startup)
    app.router.add_get("/api/auth/{action}", owner_endpoint)
    app.router.add_post("/api/auth/{action}", owner_endpoint)
