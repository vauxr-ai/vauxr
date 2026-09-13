"""Lifecycle v1 HTTPS polling API shared by firmware, browser and plugin clients."""

import asyncio
import contextlib
import json

from aiohttp import web

from auth_connections import Teardown
from auth_policy import Principal, Role
from enrollment import EnrollmentError
from enrollment_http import ENROLLMENT, unique_object
from lifecycle import Lifecycle
from owner_http import COOKIE, ORIGIN, OWNER, secure_request

LIFECYCLE = web.AppKey("lifecycle", Lifecycle)
MEDIA_TEARDOWN = web.AppKey("lifecycle_media_teardown", Teardown)


async def lifecycle_endpoint(request: web.Request) -> web.Response:
    service = request.app[LIFECYCLE]
    try:
        if (not secure_request(request) or request.query_string
                or len(request.headers.getall("Origin", [])) > 1
                or request.headers.get("Origin", request.app[ORIGIN]) != request.app[ORIGIN]):
            raise EnrollmentError("forbidden")
        request.app[ENROLLMENT].rate_limit()
        if request.content_type != "application/json" or len(request.headers.getall("Authorization", [])) > 1:
            raise EnrollmentError("invalid_request")
        header = request.headers.get("Authorization", "")
        cookie = request.cookies.get(COOKIE, "")
        if header and cookie:
            raise EnrollmentError("invalid_request")
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.content.iter_chunked(1024):
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise EnrollmentError("invalid_request")
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)

        def resolve() -> Principal | None:
            if cookie:
                result = request.app[OWNER].session(cookie)
                return result[0] if result else None
            principal = service.store.authenticate(header[7:] if header.startswith("Bearer ") else None)
            return principal if principal and principal.role != Role.OWNER else None

        result = service.execute(request.match_info["action"], body, resolve)
        response = web.json_response(result)
    except EnrollmentError as exc:
        code = str(exc)
        status = {"unauthorized": 401, "forbidden": 403, "not_found": 404,
                  "rate_limited": 429, "capacity": 429, "conflict": 409,
                  "unavailable": 409, "recovery_unavailable": 409}.get(code, 400)
        response = web.json_response({"error": code}, status=status)
    except (json.JSONDecodeError, UnicodeError, TimeoutError, RecursionError):
        response = web.json_response({"error": "invalid_request"}, status=400)
    except (OSError, ValueError):
        response = web.json_response({"error": "lifecycle_unavailable"}, status=503)
    # Also enforce the visible post-rename state when durability reported failure.
    try:
        await disconnect_stale(request.app)
    except RuntimeError:
        response = web.json_response({"error": "transport_teardown_unavailable"}, status=503)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


lifecycle_endpoint.authz_boundary = True  # type: ignore[attr-defined]


async def disconnect_stale(app: web.Application) -> None:
    from auth_connections import disconnect_stale as disconnect

    results = await asyncio.gather(disconnect(app[LIFECYCLE].store), app[MEDIA_TEARDOWN].run(),
                                   return_exceptions=True)
    if any(isinstance(result, BaseException) for result in results):
        raise RuntimeError("transport_teardown_unavailable")


async def disconnect_media(app: web.Application) -> None:
    store = app[LIFECYCLE].store
    # A plugin may have disconnected while its dependent media/turn remains.
    # Revoke must tear that down even without a retained integration socket.
    import channel_registry
    import device_registry
    from config import get_config

    active = channel_registry.get_active()
    if active is not None and active.type == "openclaw":
        records = [r for r in store.records if r.role == Role.INTEGRATION and r.subject == active.id]
        if records and not any(store.usable(r) for r in records):
            for device in device_registry.get_all():
                device_registry.abort_active_turn(device.id)
            if get_config().realtime.enabled:
                from realtime_session import get_manager

                await get_manager().stop_all()


def attach_lifecycle(app: web.Application) -> None:
    app[LIFECYCLE] = Lifecycle(app[OWNER].store, app[ORIGIN])
    app[MEDIA_TEARDOWN] = Teardown(lambda: disconnect_media(app))

    async def maintenance(application: web.Application) -> None:
        while True:
            try:
                application[LIFECYCLE].sweep()
            except (OSError, ValueError):
                pass  # Failed load clears credentials; transport checks fail closed.
            try:
                await disconnect_stale(application)
            except RuntimeError:
                pass  # Retained failures are retried; authorization already fails closed.
            await asyncio.sleep(1)

    async def context(application: web.Application):
        application[LIFECYCLE].sweep()
        task = asyncio.create_task(maintenance(application))
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app.cleanup_ctx.append(context)
    app.router.add_post("/api/lifecycle/v1/{action}", lifecycle_endpoint)
