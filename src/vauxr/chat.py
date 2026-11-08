"""Bounded browser text turns through the existing authenticated agent transport.

No speech resolution, STT, TTS, media session or new conversation store.
"""
from __future__ import annotations

import asyncio
import sys
from collections import deque
from typing import TYPE_CHECKING

from vauxr import companion
from vauxr.agents import registry as agents
from vauxr.auth import connections
from vauxr.auth.policy import Principal
from vauxr.auth.service import current, get_store
from vauxr.config import get_config
from vauxr.devices import registry as devices

if TYPE_CHECKING:
    from vauxr.agents.openclaw import OpenClawClient
    from vauxr.agents.server import AgentServer

_active: set[tuple[str, str]] = set()
_seen: deque[tuple[str, str, str, str]] = deque(maxlen=512)


class ChatError(ValueError):
    """Safe public failure; never include a peer's raw error or payload."""


def is_active(device_id: str) -> bool:
    return (get_config().data_dir, device_id) in _active


async def run_chat(
    principal: Principal, request_id: str, text: str,
    server: AgentServer, direct: OpenClawClient | None,
) -> str:
    device_id = principal.subject
    key = (get_config().data_dir, device_id)
    replay = (*key, principal.credential_id, request_id)
    agent = agents.get_for_device(device_id)
    entry = devices.get(device_id)
    module = sys.modules.get("vauxr.realtime.session")
    manager = getattr(module, "_manager", None)
    if agent is None:
        raise ChatError("Selected agent unavailable; choose an available conversation agent")
    if (key in _active or replay in _seen or len(_active) >= 128
            or server.get_response_listener(device_id) is not None
            or (entry is not None and entry.state not in ("idle", "offline"))
            or (manager is not None and manager.has_live_session(device_id))):
        raise ChatError("Request already used or browser busy. Stop voice before sending text; do not retry an uncertain action")
    _active.add(key)
    _seen.append(replay)
    owner_generation = get_store().owner.get("generation")
    future = asyncio.get_running_loop().create_future()
    response = ""
    abort = asyncio.Event()

    async def revoked() -> None:
        abort.set()
        if not future.done():
            future.set_exception(ChatError("Browser or integration access retired"))

    authority = connections.retain(principal, revoked)
    if entry is not None:
        devices.set_state(device_id, "processing")

    def delta(_run_id: str, text: str) -> None:
        nonlocal response
        if future.done():
            return
        if len(response) + len(text) > 32000:
            future.set_exception(ChatError("Text response exceeded its limit; check action status before retrying"))
        else:
            response += text

    def end(_run_id: str) -> None:
        if not future.done():
            future.set_result(response)

    def error(_run_id: str, _message: str) -> None:
        if not future.done():
            future.set_exception(ChatError("Agent request failed; check action status before retrying"))

    listener = {"on_delta": delta, "on_end": end, "on_error": error}
    local_task: asyncio.Task | None = None
    try:
        async with asyncio.timeout(120):
            if agent.type == "langchain":
                from vauxr.agents.langchain import answer
                def is_current() -> bool:
                    selected = agents.get_for_device(device_id)
                    return (not abort.is_set() and current(principal)
                            and selected is not None and selected.id == agent.id
                            and get_store().owner.get("generation") == owner_generation)
                local_task = asyncio.create_task(answer(agent.id, device_id, text, is_current=is_current))
                completed, _ = await asyncio.wait((local_task, future), return_when=asyncio.FIRST_COMPLETED)
                if future in completed:
                    await future
                response = await local_task
            elif agent.type == "openclaw-direct":
                if direct is None:
                    raise ChatError("Selected agent is not connected")

                def cumulative(text: str) -> None:
                    nonlocal response
                    if len(text) > 32000:
                        raise ChatError("Text response exceeded its limit")
                    response = text

                await direct.chat(f"vauxr:{device_id}", text + companion.untrusted(device_id), cumulative)
            else:
                server.add_response_listener(device_id, listener)
                server.retain_media_turn(agent.id, abort, revoked)
                if not server.send_transcript(device_id, text, listener=listener):
                    raise ChatError("Selected agent is not connected")
                response = await future
            selected = agents.get_for_device(device_id)
            if (abort.is_set() or not current(principal) or selected is None or selected.id != agent.id
                    or get_store().owner.get("generation") != owner_generation):
                raise ChatError("Browser authority or conversation agent changed; response discarded")
            return response
    except TimeoutError:
        raise ChatError("Text request timed out; check action status before retrying") from None
    except ChatError:
        raise
    except Exception:  # noqa: BLE001 -- peer errors must never disclose credentials
        raise ChatError("Agent request failed; check action status before retrying") from None
    finally:
        if local_task is not None and not local_task.done():
            local_task.cancel()
            await asyncio.gather(local_task, return_exceptions=True)
        server.remove_response_listener(device_id, listener)
        server.release_media_turn(abort)
        connections.release(authority)
        if not future.done():
            future.cancel()
        elif not future.cancelled():
            future.exception()  # Retrieve retirement errors on the Direct path too.
        _active.discard(key)
        if entry is not None and devices.get(device_id) is entry and entry.state == "processing":
            devices.set_state(device_id, "idle")
