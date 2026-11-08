"""Owner confirmation and exact browser credential boundary."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from aiohttp import web

from vauxr import companion
from vauxr.auth.policy import Role
from vauxr.auth.service import authenticate, get_store
from vauxr.config_files import unique_object
from vauxr.speech.store import VoiceConflict
from vauxr.speech.store import get_store as speech_store
from vauxr.web.owner import session_principal

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def attach_companion(app: web.Application, boundary: Callable[[Handler], Handler]) -> None:
    async def operation(request: web.Request) -> web.Response:
        owner = session_principal(request)
        proof = request.headers.getall("X-Vauxr-Browser", [])
        browser = authenticate(proof[0] if len(proof) == 1 else "")
        if owner is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        if (browser is None or browser.role != Role.DEVICE
                or get_store().lifecycle.get("bindings", {}).get(browser.subject, {}).get("kind") != "browser"):
            return web.json_response({"error": "forbidden"}, status=403)
        try:
            if request.query_string or request.content_type != "application/json":
                raise ValueError()
            raw = bytearray()
            async with asyncio.timeout(10):
                async for chunk in request.content.iter_chunked(1024):
                    raw.extend(chunk)
                    if len(raw) > 4096:
                        raise ValueError()
            body = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(body, dict) or body.pop("browser_id", None) != browser.subject:
                raise ValueError()
            action = request.match_info["action"]
            if action == "context":
                result = companion.publish(browser, body)
            elif action == "select":
                if set(body) != {"agent_id"} or not isinstance(body["agent_id"], str):
                    raise ValueError()
                speech_store().update(body, browser.subject)
                companion._views.pop(companion._key(browser.subject), None)
                result = {"agent_id": body["agent_id"]}
            elif action == "propose":
                result = companion.propose(browser, body)
            elif action == "confirm":
                result = companion.confirm(browser, body)
            else:
                raise web.HTTPNotFound()
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except VoiceConflict:
            return web.json_response({"error": "Resolve browser voice override conflict in Devices first"}, status=409)
        except OSError:
            return web.json_response({"error": "Save unavailable; refresh before retrying"}, status=503)
        except (ValueError, TypeError, KeyError, TimeoutError):
            return web.json_response({"error": "Context changed or invalid request; refresh before retrying"}, status=409)

    app.router.add_post("/api/companion/{action}", boundary(operation))
