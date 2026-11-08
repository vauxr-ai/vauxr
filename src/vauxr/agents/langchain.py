"""Registered LangChain runnable with one bounded, cancellation-owned tool loop."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

import aiohttp
from langchain_core.runnables import RunnableLambda
from langsmith import tracing_context

from vauxr import companion
from vauxr.agents import registry
from vauxr.agents.support import EMPTY, validate
from vauxr.config import get_config
from vauxr.config_files import read_json
from vauxr.plugins.agents import build
from vauxr.web import webhooks

log = logging.getLogger(__name__)


class State(TypedDict):
    messages: list[dict]
    calls: list[dict]
    rounds: int
    reply: str


async def _response_json(response: aiohttp.ClientResponse) -> object:
    """Read all network fragments while enforcing the response byte limit."""
    raw = bytearray()
    async for chunk in response.content.iter_chunked(16384):
        raw.extend(chunk)
        if len(raw) > 131072:
            raise ValueError("Runtime response exceeds limit")
    return json.loads(raw)


def settings(agent_id: str) -> dict[str, str]:
    path = Path(get_config().data_dir) / "local-agent-models.json"
    agent = registry.get_by_id(agent_id)
    if agent is not None and agent.definition_id == "vauxr-support" and agent.id == "vauxr-support":
        from vauxr.agents.support import settings as support_settings

        return support_settings()
    return validate(read_json(path).get(agent_id, EMPTY)) if path.exists() else dict(EMPTY)


_history: OrderedDict[tuple[str, str, str], list[dict]] = OrderedDict()
_pending: set[tuple[str, str, str]] = set()


async def answer(
    agent_id: str,
    device_id: str,
    text: str,
    *,
    is_current: Callable[[], bool] | None = None,
) -> str:
    key = (get_config().data_dir, agent_id, device_id)
    if key in _pending or len(_pending) >= 128:
        return "Agent busy; check the current action before retrying."
    _pending.add(key)
    try:
        # Agent prompts/tool results must never leave through ambient tracing config.
        with tracing_context(enabled=False):
            return await _answer(agent_id, device_id, text, key, is_current or (lambda: True))
    finally:
        _pending.discard(key)


async def _answer(
    agent_id: str,
    device_id: str,
    text: str,
    key: tuple[str, str, str],
    is_current: Callable[[], bool],
) -> str:
    def check_owner() -> None:
        if not is_current():
            raise RuntimeError("Agent turn retired; check action state before retrying")

    check_owner()
    agent = registry.get_by_id(agent_id)
    if agent is None or agent.type != "langchain":
        raise ValueError("Registered agent unavailable")
    implementation = build(agent.definition_id or "")
    config = settings(agent_id)
    if not config["provider"]:
        return f"{agent.name} needs setup. Configure its local model runtime in Settings. No model request was made."
    tools = [
        {
            "type": "function",
            "function": {
                "name": "vauxr_household_list",
                "description": "List configured household action IDs and names only; credentials stay in Vauxr.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "vauxr_household_action",
                "description": "Deliver the user's requested named household action through Vauxr. HTTP delivery is not proof of the external device state.",
                "parameters": {
                    "type": "object",
                    "properties": {"webhook_id": {"type": "string"}},
                    "required": ["webhook_id"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    if not implementation["household"]:
        tools = []
    if companion.turn_context(device_id) is not None:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "vauxr_context_action",
                    "description": "Requested navigation, highlight or selected-agent rename. Use the fresh exact UI revision/resource ID from the supplied context; no second confirmation.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": [
                                    "navigate",
                                    "highlight",
                                    "rename_agent",
                                    "interface_read",
                                    "interface_save",
                                ],
                            },
                            "document_revision": {"type": "string"},
                            "title": {"type": "string"},
                            "html": {"type": "string"},
                            "css": {"type": "string"},
                            "revision": {"type": "string"},
                            "resource_id": {"type": "string"},
                            "name": {"type": "string"},
                            "route": {"type": "string", "enum": ["agents", "devices", "settings"]},
                            "control": {"type": "string", "enum": ["agent_name"]},
                        },
                        "required": ["op", "revision"],
                        "additionalProperties": False,
                    },
                },
            }
        )
    if companion.turn_context(device_id) is not None:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "vauxr_context_read",
                    "description": "Read fresh sanitized UI context.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            }
        )
    tool_names = {tool["function"]["name"] for tool in tools}
    phase = "metadata"
    try:
        async with (
            asyncio.timeout(90),
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), trust_env=False) as session,
        ):
            async with session.post(
                config["endpoint"] + "/api/show", json={"model": config["model"]}, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise ValueError()
                metadata = await _response_json(response)
                if not isinstance(metadata, dict):
                    raise TypeError()
                if metadata.get("remote_host") or metadata.get("remote_model"):
                    raise ValueError()

            delivered_actions: dict[str, dict] = {}
            context_actions: dict[str, dict] = {}

            async def model(state: State) -> dict:
                async with session.post(
                    config["endpoint"] + "/api/chat",
                    json={
                        "model": config["model"],
                        "stream": False,
                        "think": False,
                        "options": {"num_predict": 2048},
                        "messages": state["messages"],
                        "tools": tools,
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise ValueError()
                    result = await _response_json(response)
                if not isinstance(result, dict) or not isinstance(result.get("message"), dict):
                    raise TypeError()
                message = result["message"]
                calls = message.get("tool_calls", [])
                reply = message.get("content", "")
                if (
                    result.get("done") is not True
                    or not isinstance(calls, list)
                    or len(calls) > 4
                    or not isinstance(reply, str)
                    or len(reply) > 32000
                ):
                    raise ValueError()
                return {
                    "messages": [*state["messages"], message],
                    "calls": calls,
                    "rounds": state["rounds"] + 1,
                    "reply": reply,
                }

            async def execute_tools(state: State) -> dict:
                messages = list(state["messages"])
                for call in state["calls"]:
                    check_owner()
                    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                        raise TypeError()
                    function = call["function"]
                    name, args = function.get("name"), function.get("arguments")
                    try:
                        if not isinstance(name, str) or name not in tool_names:
                            raise ValueError()
                        if name == "vauxr_context_read" and args == {}:
                            result = companion.turn_context(device_id) or {"error": "UI context unavailable"}
                        elif name == "vauxr_household_list" and args == {}:
                            result = {
                                "actions": [{"id": hook.id, "name": hook.name} for hook in webhooks.get_all()]
                            }
                        elif (
                            name == "vauxr_household_action"
                            and isinstance(args, dict)
                            and set(args) == {"webhook_id"}
                            and isinstance(args["webhook_id"], str)
                        ):
                            key = args["webhook_id"]
                            if key in delivered_actions:
                                result = {**delivered_actions[key], "reused_result": True}
                            else:
                                result = await webhooks.execute_named(key, device_id)
                                delivered_actions[key] = result
                        elif name == "vauxr_context_action" and isinstance(args, dict):
                            action_key = json.dumps(args, sort_keys=True)
                            if action_key in context_actions:
                                result = {**context_actions[action_key], "reused_result": True}
                            else:
                                result = await companion.execute(device_id, args)
                                context_actions[action_key] = result
                        else:
                            raise ValueError()
                    except (ValueError, TypeError, KeyError, OSError, TimeoutError):
                        result = {
                            "error": "Action unavailable or context changed; check state before retrying"
                        }
                    messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result)})
                return {"messages": messages, "calls": []}

            state: State = {
                "messages": [
                    {"role": "system", "content": implementation["system"]},
                    *_history.get(key, []),
                    {"role": "user", "content": text + companion.untrusted(device_id)},
                ],
                "calls": [],
                "rounds": 0,
                "reply": "",
            }
            model_step = RunnableLambda(model)
            tool_step = RunnableLambda(execute_tools)
            for _ in range(4):
                check_owner()
                phase = "model"
                state.update(await model_step.ainvoke(state))
                check_owner()
                if not state["calls"]:
                    reply = state["reply"]
                    if not reply:
                        raise ValueError()
                    # Runtime owns local conversation history; transports never replay it.
                    # Store only completed turns; cancellation cannot commit a partial turn.
                    _history[key] = [
                        *_history.get(key, []),
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": reply},
                    ][-20:]
                    while sum(len(m["content"]) for m in _history[key]) > 64000:
                        del _history[key][:2]
                    _history.move_to_end(key)
                    while len(_history) > 128:
                        _history.popitem(last=False)
                    return reply
                if state["rounds"] == 4:
                    break
                phase = "tools"
                state.update(await tool_step.ainvoke(state))
            return "The agent reached its tool limit. Check action status before requesting more work."
    except (aiohttp.ClientError, ValueError, TypeError, KeyError, TimeoutError) as error:
        log.warning("Local LangChain runtime failed during %s (%s)", phase, type(error).__name__)
        return "The LangChain agent's configured local runtime is unavailable. Check its endpoint and installed local model."
