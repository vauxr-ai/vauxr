"""Owner-cookie/CSRF protected telemetry decision and exact payload preview."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from aiohttp import web

from vauxr.config import get_config
from vauxr.config_files import unique_object
from vauxr.web.owner import session_principal

from .runtime import Telemetry

KEY = web.AppKey('telemetry', Telemetry)


def attach_telemetry(app, boundary):
    async def lifecycle(app):
        from vauxr import telemetry
        from vauxr.devices import registry
        runtime = Telemetry(Path(get_config().data_dir), device_count=lambda: len(registry.get_all()))
        app[KEY] = runtime
        telemetry._current = runtime
        await runtime.start()
        try:
            yield
        finally:
            if telemetry._current is runtime:
                telemetry._current = None
            await runtime.close()

    async def operation(request):
        if session_principal(request) is None:
            return web.json_response({'error': 'unauthorized'}, status=401)
        try:
            if request.query_string:
                raise ValueError()
            runtime = request.app[KEY]
            if request.method == 'GET':
                result = await runtime.view()
            else:
                if request.content_type != 'application/json':
                    raise ValueError()
                raw = bytearray()
                async with asyncio.timeout(5):
                    async for chunk in request.content.iter_chunked(256):
                        raw.extend(chunk)
                        if len(raw) > 1024:
                            raise ValueError()
                body = json.loads(raw, object_pairs_hook=unique_object)
                if (type(body) is not dict or set(body) != {'enabled', 'acknowledged'}
                        or type(body['enabled']) is not bool or body['acknowledged'] is not True):
                    raise ValueError()
                result = await runtime.decide(body['enabled'])
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except (ValueError, TypeError, TimeoutError):
            return web.json_response({'error': 'Invalid telemetry decision'}, status=400)
        except (OSError, sqlite3.Error):
            return web.json_response({'error': 'Telemetry storage unavailable; refresh before retrying'}, status=503)

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get('/api/settings/telemetry', boundary(operation))
    app.router.add_put('/api/settings/telemetry', boundary(operation))
