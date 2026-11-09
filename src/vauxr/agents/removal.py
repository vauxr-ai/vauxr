"""Owner agent removal reuses credential retirement and the existing routing stores."""

import secrets
from pathlib import Path

from aiohttp import web

from vauxr.agents import registry
from vauxr.agents.avatars import remove as remove_avatar
from vauxr.config import get_config
from vauxr.config_files import update_json
from vauxr.provisioning.enrollment import EnrollmentError
from vauxr.provisioning.lifecycle_http import LIFECYCLE, disconnect_stale
from vauxr.speech.store import VoiceConflict, get_store
from vauxr.web.owner import session_principal


async def remove_agent(request: web.Request, agent_id: str) -> web.Response:
    """Retire an inactive agent across authority, routing and auxiliary stores.

    This is not a multi-file transaction: 503 can follow partial durable progress.
    Refresh inventory before retrying; integration teardown must finish before
    success is reported. The caller supplies the owner/CSRF boundary.
    """
    agent = registry.get_by_id(agent_id)
    if agent is None:
        return web.json_response({"error": "Agent not found"}, status=404)
    if agent.active:
        return web.json_response({"error": "Set another agent as default before deleting this agent"}, status=409)
    if agent.builtin and agent.definition_id != "vauxr-support":
        return web.json_response({"error": "Bundled agent cannot be deleted"}, status=409)
    try:
        if agent.id == "vauxr-support":
            update_json(Path(get_config().data_dir) / "support-installation.json", lambda _: {"removed": True})
        get_store().detach_agent(agent_id)
        if agent.integration:
            request.app[LIFECYCLE].execute("revoke", {
                "operation_id": secrets.token_hex(16), "role": "integration", "subject": agent_id,
            }, lambda: session_principal(request))
            await disconnect_stale(request.app)
        if registry.get_by_id(agent_id) is not None:
            registry.remove(agent_id)
        remove_avatar(agent_id)
        path = Path(get_config().data_dir) / "local-agent-models.json"
        if path.exists():
            update_json(path, lambda current: {key: value for key, value in current.items() if key != agent_id})
        return web.json_response({"removed": True})
    except VoiceConflict as error:
        return web.json_response({"error": str(error)}, status=409)
    except (OSError, ValueError, RuntimeError, EnrollmentError):
        return web.json_response({"error": "Agent removal did not complete. Refresh agents before retrying."}, status=503)
