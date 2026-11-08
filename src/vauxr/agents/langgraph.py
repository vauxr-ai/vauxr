"""Standalone local-model LangGraph integration; no OpenClaw dependency."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TypedDict

import aiohttp
from langgraph.graph import END, START, StateGraph

from vauxr import companion
from vauxr.agents.support import EMPTY, validate
from vauxr.config import get_config
from vauxr.config_files import read_json
from vauxr.web import webhooks


class State(TypedDict):
    messages: list[dict]
    calls: list[dict]
    rounds: int
    reply: str


def settings(agent_id: str) -> dict[str, str]:
    path = Path(get_config().data_dir) / "local-agent-models.json"
    return validate(read_json(path).get(agent_id, EMPTY)) if path.exists() else dict(EMPTY)


async def answer(agent_id: str, device_id: str, text: str) -> str:
    config = settings(agent_id)
    if not config["provider"]:
        return "This LangGraph agent needs setup. Configure its own local model runtime in Agents. No model request was made."
    tools = [
        {"type":"function", "function":{"name":"vauxr_household_list", "description":"List configured household action IDs and names only; credentials stay in Vauxr.", "parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        {"type":"function", "function":{"name":"vauxr_household_action", "description":"Deliver the user's requested named household action through Vauxr. HTTP delivery is not proof of the external device state.", "parameters":{"type":"object","properties":{"webhook_id":{"type":"string"}},"required":["webhook_id"],"additionalProperties":False}}},
    ]
    if companion.turn_context(device_id) is not None:
        tools.append({"type":"function", "function":{"name":"vauxr_context_action", "description":"Requested navigation, highlight or selected-agent rename. Use the fresh exact UI revision/resource ID from the supplied context; no second confirmation.", "parameters":{"type":"object","properties":{"op":{"type":"string","enum":["read","navigate","highlight","rename_agent"]},"revision":{"type":"string"},"resource_id":{"type":"string"},"name":{"type":"string"},"route":{"type":"string","enum":["agents","devices","settings"]},"control":{"type":"string","enum":["agent_name"]}},"required":["op","revision"],"additionalProperties":False}}})
    try:
        async with asyncio.timeout(90), aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), trust_env=False) as session:
            async with session.post(config["endpoint"] + "/api/show", json={"model":config["model"]}, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError()
                metadata = await response.json()
                if metadata.get("remote_host") or metadata.get("remote_model"):
                    raise ValueError()

            delivered_actions: dict[str, dict] = {}

            async def model(state: State) -> dict:
                async with session.post(config["endpoint"] + "/api/chat", json={"model":config["model"],"stream":False,"messages":state["messages"],"tools":tools}, allow_redirects=False) as response:
                    raw = await response.content.read(131073)
                    if response.status != 200 or len(raw) > 131072:
                        raise ValueError()
                    result = json.loads(raw)
                message = result.get("message", {})
                calls = message.get("tool_calls", [])
                reply = message.get("content", "")
                if (result.get("done") is not True or not isinstance(calls, list) or len(calls) > 4
                        or not isinstance(reply, str) or len(reply) > 32000):
                    raise ValueError()
                return {"messages":[*state["messages"],message],"calls":calls,"rounds":state["rounds"]+1,"reply":reply}

            async def execute_tools(state: State) -> dict:
                messages = list(state["messages"])
                for call in state["calls"]:
                    function = call.get("function", {})
                    name, args = function.get("name"), function.get("arguments")
                    try:
                        if name == "vauxr_household_list" and args == {}:
                            result = {"actions":[{"id":hook.id,"name":hook.name} for hook in webhooks.get_all()]}
                        elif (name == "vauxr_household_action" and isinstance(args, dict)
                              and set(args) == {"webhook_id"} and isinstance(args["webhook_id"], str)):
                            key = args["webhook_id"]
                            if key in delivered_actions:
                                result = {**delivered_actions[key], "reused_result":True}
                            else:
                                result = await webhooks.execute_named(key, device_id)
                                delivered_actions[key] = result
                        elif name == "vauxr_context_action" and isinstance(args, dict):
                            result = await companion.execute(device_id, args)
                        else:
                            raise ValueError()
                    except (ValueError, TypeError, KeyError, OSError, TimeoutError):
                        result = {"error":"Action unavailable or context changed; check state before retrying"}
                    messages.append({"role":"tool","tool_name":name,"content":json.dumps(result)})
                return {"messages":messages,"calls":[]}

            graph = StateGraph(State)
            graph.add_node("model", model)
            graph.add_node("tools", execute_tools)
            graph.add_edge(START, "model")
            graph.add_conditional_edges("model", lambda state: "tools" if state["calls"] and state["rounds"] < 4 else END)
            graph.add_edge("tools", "model")
            result = await graph.compile().ainvoke({"messages":[
                {"role":"system","content":"You are a standalone household LangGraph agent hosted by Vauxr. Use only supplied tools for explicitly requested changes. Never invent action success. HTTP delivery is not proof of external state; report that distinction. No shell/source/credential access. Be concise for voice."},
                {"role":"user","content":text+companion.untrusted(device_id)},
            ],"calls":[],"rounds":0,"reply":""}, {"recursion_limit":10})
            return result["reply"] or "The graph reached its tool limit. Check action status before requesting more work."
    except (aiohttp.ClientError, ValueError, TimeoutError):
        return "The LangGraph agent's configured local runtime is unavailable. Check its endpoint and installed local model."
