"""Owner administration for implemented local integrations, without credential minting."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import web

from vauxr import companion
from vauxr.agents import registry
from vauxr.agents.support import runtime_view, validate
from vauxr.config import get_config
from vauxr.config_files import unique_object, update_json
from vauxr.plugins.agents import definition, definitions
from vauxr.web.owner import session_principal


def attach_local_agents(
    app: web.Application,
    boundary: Callable[
        [Callable[[web.Request], Awaitable[web.StreamResponse]]],
        Callable[[web.Request], Awaitable[web.StreamResponse]],
    ],
) -> None:
    async def handle(request: web.Request) -> web.Response:
        if session_principal(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            if request.query_string:
                raise ValueError()
            body = {}
            if request.method == "POST":
                if request.content_type != "application/json":
                    raise ValueError()
                async with asyncio.timeout(10):
                    raw = await request.content.read(4097)
                if len(raw) > 4096:
                    raise ValueError()
                body = json.loads(raw, object_pairs_hook=unique_object)
                if not isinstance(body, dict):
                    raise ValueError()
            agent_id = request.match_info.get("agent_id")
            if agent_id is None:
                if request.method == "GET":
                    return web.json_response(
                        {"definitions": definitions()}, headers={"Cache-Control": "no-store"}
                    )
                if set(body) != {"definition_id", "name"}:
                    raise ValueError()
                try:
                    selected = definition(body["definition_id"])
                except StopIteration:
                    raise ValueError() from None
                if selected["framework"] != "langchain":
                    raise ValueError()
                name = body["name"]
                if not companion._safe_name(name):
                    raise ValueError()
                agent = registry.Agent(
                    secrets.token_hex(16),
                    name.strip(),
                    "langchain",
                    False,
                    datetime.now(UTC).isoformat(),
                    definition_id=selected["id"],
                )
                registry.register(agent)
                return web.json_response(asdict(agent), status=201, headers={"Cache-Control": "no-store"})
            agent = registry.get_by_id(agent_id)
            if agent is None or agent.type != "langchain":
                return web.json_response({"error": "Registered LangChain agent not found"}, status=404)
            path = Path(get_config().data_dir) / "local-agent-models.json"
            if request.method == "DELETE":
                from vauxr.agents.removal import remove_agent

                return await remove_agent(request, agent_id)
            if request.method == "POST":
                config = validate(body)
                if agent_id == "vauxr-support":
                    update_json(Path(get_config().data_dir) / "support.json", lambda _: config)
                else:
                    update_json(path, lambda current: {**current, agent_id: config})
            from vauxr.agents.langchain import settings

            config = settings(agent_id)
            return web.json_response(
                runtime_view(config),
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, TypeError, KeyError, TimeoutError):
            return web.json_response(
                {
                    "error": "Use a supported local framework, valid display name and explicit local runtime settings"
                },
                status=400,
            )
        except OSError:
            return web.json_response({"error": "Save unavailable; reload before retrying"}, status=503)

    app.router.add_get("/api/local-agents", boundary(handle))
    app.router.add_post("/api/local-agents", boundary(handle))
    app.router.add_get("/api/local-agents/{agent_id}/runtime", boundary(handle))
    app.router.add_post("/api/local-agents/{agent_id}/runtime", boundary(handle))
    app.router.add_delete("/api/local-agents/{agent_id}", boundary(handle))
