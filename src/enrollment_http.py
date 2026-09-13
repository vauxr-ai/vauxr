"""Enrollment endpoints share the configured owner HTTP/HTTPS/proxy/cookie boundary."""

import asyncio
import json

from aiohttp import web

from auth_policy import Principal, Role
from enrollment import Enrollment, EnrollmentError
from owner_http import ORIGIN, OWNER, cookie_name, secure_request

ENROLLMENT = web.AppKey("enrollment", Enrollment)
CLIENT_ACTIONS = {"request", "prove", "redeem", "cancel", "status"}


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnrollmentError("invalid_request")
        result[key] = value
    return result


async def enrollment_endpoint(request: web.Request) -> web.Response:
    try:
        if (
            not secure_request(request)
            or request.query_string
            or len(request.headers.getall("Origin", [])) > 1
            or request.headers.get("Origin", request.app[ORIGIN]) != request.app[ORIGIN]
        ):
            raise EnrollmentError("forbidden")
        service = request.app[ENROLLMENT]
        service.rate_limit()
        if request.content_type != "application/json":
            raise EnrollmentError("invalid_request")
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.content.iter_chunked(1024):
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise EnrollmentError("invalid_request")
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
        action = request.match_info["action"]
        if len(request.headers.getall("Authorization", [])) > 1:
            raise EnrollmentError("invalid_request")
        header = request.headers.get("Authorization", "")
        cookie = request.cookies.get(cookie_name(request), "")
        if header and (cookie or action in CLIENT_ACTIONS):
            raise EnrollmentError("invalid_request")

        def resolve() -> Principal | None:
            # execute() holds the shared transaction throughout this check and write.
            if cookie:
                result = request.app[OWNER].session(cookie)
                return result[0] if result else None
            principal = service.store.authenticate(header[7:] if header.startswith("Bearer ") else None)
            return principal if principal is not None and principal.role != Role.OWNER else None

        response = web.json_response(service.execute(action, body, resolve))
    except EnrollmentError as exc:
        code = str(exc)
        status = {
            "unauthorized": 401,
            "forbidden": 403,
            "not_found": 404,
            "rate_limited": 429,
            "capacity": 429,
            "already_owned": 409,
            "conflict": 409,
            "unavailable": 409,
        }.get(code, 400)
        response = web.json_response({"error": code}, status=status)
    except (json.JSONDecodeError, UnicodeError, TimeoutError, RecursionError):
        response = web.json_response({"error": "invalid_request"}, status=400)
    except (OSError, ValueError):
        response = web.json_response({"error": "enrollment_unavailable"}, status=503)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


enrollment_endpoint.authz_boundary = True  # type: ignore[attr-defined]


def attach_enrollment(app: web.Application) -> None:
    app[ENROLLMENT] = Enrollment(app[OWNER].store, app[ORIGIN])

    async def startup(application: web.Application) -> None:
        application[ENROLLMENT].initialize()

    app.on_startup.append(startup)
    app.router.add_post("/api/enrollment/v1/{action}", enrollment_endpoint)
