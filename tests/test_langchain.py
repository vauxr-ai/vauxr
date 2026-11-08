"""Standalone graph, independent provider configuration and household delivery boundary."""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import owner_headers
from vauxr.agents import langchain, registry
from vauxr.agents.server import AgentServer
from vauxr.config import reset_config
from vauxr.config_files import update_json
from vauxr.web import webhooks
from vauxr.web.server import make_http_app


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    reset_config()
    registry._reset_for_tests()
    webhooks.reset_for_tests()
    yield
    reset_config()
    registry._reset_for_tests()
    webhooks.reset_for_tests()


async def test_owner_local_agent_flow_never_mints_integration_credentials():
    async with TestClient(TestServer(make_http_app())) as client:
        assert (
            await client.post("/api/local-agents", json={"definition_id": "household", "name": "Household"})
        ).status == 401
        headers = owner_headers(client)
        response = await client.post(
            "/api/local-agents", headers=headers, json={"definition_id": "household", "name": "Household"}
        )
        assert response.status == 201
        agent = await response.json()
        assert agent["type"] == "langchain" and agent["integration"] is False
        assert "token" not in json.dumps(agent).lower()
        assert "needs setup" in await langchain.answer(agent["id"], "D1", "Help")
        from vauxr.speech.store import get_store

        store = get_store()
        before = store.path.read_bytes() if store.path.exists() else None
        with pytest.raises(ValueError, match="does not support"):
            store.update(
                {"enabled_modes": ["standard", "realtime"], "default_mode": "standard"}, agent_id=agent["id"]
            )
        assert (store.path.read_bytes() if store.path.exists() else None) == before
        path = f"/api/local-agents/{agent['id']}/runtime"
        assert (await (await client.get(path, headers=headers)).json())["status"] == "setup_needed"
        assert (
            await client.post(
                path,
                headers=headers,
                json={"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "local"},
            )
        ).status == 200
        registry.activate(agent["id"])
        assert (await client.delete(f"/api/local-agents/{agent['id']}", headers=headers)).status == 409
        registry.ensure_support()
        registry.activate("vauxr-support")
        assert (await client.delete(f"/api/local-agents/{agent['id']}", headers=headers)).status == 200


@pytest.mark.parametrize("use_profile", [False, True])
async def test_real_graph_routes_through_shared_agent_transport_and_named_household_action(
    tmp_path, unused_tcp_port, use_profile
):
    model_requests = []
    deliveries = []

    async def show(_):
        return web.json_response({"details": {}})

    async def model(request):
        body = await request.json()
        model_requests.append(body)
        assert "household-secret" not in json.dumps(body)
        assert "/household-endpoint" not in json.dumps(body)
        n = len(model_requests)
        if n == 1:
            message = {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "vauxr_household_list", "arguments": {}}}],
            }
        elif n == 2:
            listing = json.loads(body["messages"][-1]["content"])
            assert listing == {"actions": [{"id": hook.id, "name": "Household lights"}]}
            message = {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "vauxr_household_action", "arguments": {"webhook_id": hook.id}}}
                ],
            }
        elif n == 3:
            message = {
                "role": "assistant",
                "tool_calls": [
                    {"function": {"name": "vauxr_household_action", "arguments": {"webhook_id": hook.id}}}
                ],
            }
        else:
            outcome = json.loads(body["messages"][-1]["content"])
            assert outcome == {
                "delivered": True,
                "http_status": 200,
                "external_state_verified": False,
                "reused_result": True,
            }
            message = {
                "role": "assistant",
                "content": "The household request was delivered; external state is not verified.",
            }
        return web.json_response({"done": True, "message": message})

    async def household(request):
        assert request.headers["Authorization"] == "Bearer household-secret"
        deliveries.append(await request.json())
        return web.json_response({"private_response": "household-secret"})

    app = web.Application()
    app.router.add_post("/api/show", show)
    app.router.add_post("/api/chat", model)
    app.router.add_post("/household-endpoint", household)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    hook = webhooks.create(
        "Household lights",
        f"http://127.0.0.1:{unused_tcp_port}/household-endpoint",
        "Bearer household-secret",
        {"entity_id": "light.fixture"},
    )
    agent = registry.Agent(
        "a" * 32, "Graph household", "langchain", False, "created", definition_id="household"
    )
    registry.register(agent)
    registry.activate(agent.id)
    update_json(
        Path(tmp_path) / "local-agent-models.json",
        lambda _: {
            agent.id: {
                "provider": "ollama",
                "endpoint": f"http://127.0.0.1:{unused_tcp_port}",
                "model": "installed-local",
            }
        },
    )
    if use_profile:
        from vauxr.settings.providers import ProviderStore

        ProviderStore(tmp_path).save("household", {
            "name": "Household fixture", "provider": "ollama",
            "endpoint": f"http://127.0.0.1:{unused_tcp_port}", "model": "installed-local", "auth": "none",
        })
        update_json(tmp_path / "local-agent-models.json", lambda _: {agent.id: {"profile_id": "household"}})
    assert not (Path(tmp_path) / "support.json").exists()
    server = AgentServer()
    done = asyncio.get_running_loop().create_future()
    chunks = []
    listener = {
        "on_delta": lambda _id, text: chunks.append(text),
        "on_end": lambda _id: done.set_result(True),
        "on_error": lambda _id, error: done.set_exception(RuntimeError(error)),
    }
    server.add_response_listener("D1", listener)
    try:
        assert server.send_transcript("D1", "Use the household lights action", listener=listener)
        await asyncio.wait_for(done, 10)
        assert "request was delivered" in "".join(chunks)
        assert len(model_requests) == 4 and deliveries == [{"entity_id": "light.fixture"}]
    finally:
        server.remove_response_listener("D1", listener)
        await runner.cleanup()


def test_migration_preserves_identity_runtime_assignments_and_policy(tmp_path):
    legacy = {
        "version": 1,
        "active_agent": "old-household",
        "agents": [
            {"id": "old-household", "name": "Kitchen", "type": "langgraph", "createdAt": "created"},
            {
                "id": "vauxr-support",
                "name": "Help",
                "type": "support",
                "createdAt": "created",
                "builtin": True,
            },
        ],
    }
    from vauxr.speech.models import Backend
    from vauxr.speech.store import SpeechStore

    speech = SpeechStore(
        tmp_path,
        (
            Backend("stt", "stt", "whisper", "small", "127.0.0.1", 10300),
            Backend("tts", "tts", "piper", "piper", "127.0.0.1", 10200, ("amy",)),
        ),
    )
    speech_data = json.loads(speech.path.read_text())
    speech_data.update(
        version=2,
        assignments={"device-a": "old-household"},
        agents={
            "old-household": {"enabled_modes": ["standard"], "default_mode": "standard", "selection": {}}
        },
        capabilities={"device-a": ["ws"]},
    )
    update_json(tmp_path / "agents.json", lambda _: legacy)
    untouched = {
        "local-agent-models.json": {
            "old-household": {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "local"}
        },
        "support.json": {
            "provider": "ollama",
            "endpoint": "http://127.0.0.1:11434",
            "model": "support-local",
        },
        "speech.json": speech_data,
        "webhooks.json": {"household": "unchanged"},
    }
    for filename, data in untouched.items():
        update_json(tmp_path / filename, lambda _, data=data: data)
    before = {name: (tmp_path / name).read_bytes() for name in untouched}
    registry.load()
    registry.ensure_support()
    assert registry.get_active().id == "old-household"
    assert [(a.id, a.name, a.type, a.definition_id) for a in registry.get_all()] == [
        ("old-household", "Kitchen", "langchain", "household"),
        ("vauxr-support", "Help", "langchain", "vauxr-support"),
    ]
    assert langchain.settings("old-household")["model"] == "local"
    assert langchain.settings("vauxr-support")["model"] == "support-local"
    restored = SpeechStore(tmp_path, ())
    assert restored.agent_id("device-a") == "old-household"
    assert restored.available_modes("device-a") == ["standard"]
    assert restored.resolve("device-a").tts.id == "tts"
    migrated = (tmp_path / "agents.json").read_bytes()
    registry.load()
    registry.ensure_support()
    assert (tmp_path / "agents.json").read_bytes() == migrated
    assert all((tmp_path / name).read_bytes() == value for name, value in before.items())


async def test_definition_discovery_and_support_instantiation_same_contract():
    async with TestClient(TestServer(make_http_app())) as client:
        headers = owner_headers(client)
        assert (await client.get("/api/local-agents")).status == 401
        listing = await (await client.get("/api/local-agents", headers=headers)).json()
        assert {row["id"] for row in listing["definitions"]} == {"household", "vauxr-support"}
        registry.ensure_support()
        assert (await client.delete("/api/local-agents/vauxr-support", headers=headers)).status == 409
        assert registry.get_by_id("vauxr-support") is not None
        for row in listing["definitions"]:
            response = await client.post(
                "/api/local-agents", headers=headers, json={"definition_id": row["id"], "name": row["name"]}
            )
            assert response.status == 201
            instance = await response.json()
            assert instance["definition_id"] == row["id"]
            assert instance["type"] == "langchain"
        assert (
            await client.post(
                "/api/local-agents", headers=headers, json={"definition_id": "../../bad", "name": "Bad"}
            )
        ).status == 400


async def test_cancellation_releases_pending_without_committing_history(tmp_path, unused_tcp_port):
    started = asyncio.Event()

    async def show(_):
        return web.json_response({})

    async def model(_):
        started.set()
        await asyncio.sleep(10)
        return web.json_response({"done": True, "message": {"content": "late"}})

    app = web.Application()
    app.router.add_post("/api/show", show)
    app.router.add_post("/api/chat", model)
    async with TestServer(app, port=unused_tcp_port):
        agent = registry.Agent("cancel", "Cancel", "langchain", False, "created", definition_id="household")
        registry.register(agent)
        update_json(
            tmp_path / "local-agent-models.json",
            lambda _: {
                agent.id: {
                    "provider": "ollama",
                    "endpoint": f"http://127.0.0.1:{unused_tcp_port}",
                    "model": "local",
                }
            },
        )
        task = asyncio.create_task(langchain.answer(agent.id, "D1", "Hello"))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        key = (str(tmp_path), agent.id, "D1")
        assert key not in langchain._pending and key not in langchain._history


async def test_fragmented_http_and_history_isolation(tmp_path, unused_tcp_port):
    received = []

    async def fragmented(request, data):
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        raw = json.dumps(data).encode()
        await response.write(raw[:8])
        await asyncio.sleep(0.01)
        await response.write(raw[8:])
        await response.write_eof()
        return response

    async def show(request):
        return await fragmented(request, {"details": {"local": True}})

    async def model(request):
        body = await request.json()
        received.append(body)
        return await fragmented(request, {"done": True, "message": {"content": "A completed reply"}})

    app = web.Application()
    app.router.add_post("/api/show", show)
    app.router.add_post("/api/chat", model)
    async with TestServer(app, port=unused_tcp_port):
        for agent_id in ("first", "second"):
            registry.register(
                registry.Agent(agent_id, agent_id, "langchain", False, "created", definition_id="household")
            )
        update_json(
            tmp_path / "local-agent-models.json",
            lambda _: {
                agent_id: {
                    "provider": "ollama",
                    "endpoint": f"http://127.0.0.1:{unused_tcp_port}",
                    "model": "local",
                }
                for agent_id in ("first", "second")
            },
        )
        for agent_id, device_id, text in [
            ("first", "D1", "Remember blue"),
            ("first", "D1", "Which color?"),
            ("first", "D2", "Another device"),
            ("second", "D1", "Another agent"),
        ]:
            assert await langchain.answer(agent_id, device_id, text) == "A completed reply"
        assert received[1]["messages"][1:3] == [
            {"role": "user", "content": "Remember blue"},
            {"role": "assistant", "content": "A completed reply"},
        ]
        assert len(received[2]["messages"]) == len(received[3]["messages"]) == 2


async def test_transport_replacement_cancels_old_turn_and_preserves_new_owner(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    completed = asyncio.Event()

    async def answer(_agent, _device, text, **_kwargs):
        if text == "old":
            started.set()
            try:
                await asyncio.sleep(20)
            finally:
                cancelled.set()
        return text

    monkeypatch.setattr(langchain, "answer", answer)
    registry.register(
        registry.Agent("replace", "Replace", "langchain", False, "created", definition_id="household")
    )
    registry.activate("replace")
    server = AgentServer()
    old_chunks = []
    new_chunks = []
    old = {
        "on_delta": lambda _, text: old_chunks.append(text),
        "on_end": lambda _: None,
        "on_error": lambda *_: None,
    }
    new = {
        "on_delta": lambda _, text: new_chunks.append(text),
        "on_end": lambda _: completed.set(),
        "on_error": lambda *_: None,
    }
    server.add_response_listener("D1", old)
    assert server.send_transcript("D1", "old", listener=old)
    await started.wait()
    server.add_response_listener("D1", new)
    assert server.send_transcript("D1", "new", listener=new)
    server.remove_response_listener("D1", old)
    await asyncio.wait_for(completed.wait(), 2)
    assert cancelled.is_set() and old_chunks == [] and new_chunks == ["new"]
    assert server.get_response_listener("D1") is new


async def test_retired_owner_cannot_dispatch_model_tool(tmp_path, unused_tcp_port, monkeypatch):
    from unittest.mock import AsyncMock

    current = True
    dispatch = AsyncMock()
    monkeypatch.setattr(webhooks, "execute_named", dispatch)

    async def show(_):
        return web.json_response({})

    async def model(_):
        nonlocal current
        current = False
        return web.json_response(
            {
                "done": True,
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "vauxr_household_action",
                                "arguments": {"webhook_id": "must-not-run"},
                            }
                        }
                    ]
                },
            }
        )

    app = web.Application()
    app.router.add_post("/api/show", show)
    app.router.add_post("/api/chat", model)
    async with TestServer(app, port=unused_tcp_port):
        registry.register(
            registry.Agent("retired", "Retired", "langchain", False, "created", definition_id="household")
        )
        update_json(
            tmp_path / "local-agent-models.json",
            lambda _: {
                "retired": {
                    "provider": "ollama",
                    "endpoint": f"http://127.0.0.1:{unused_tcp_port}",
                    "model": "local",
                }
            },
        )
        with pytest.raises(RuntimeError, match="retired"):
            await langchain.answer("retired", "D1", "Act", is_current=lambda: current)
        dispatch.assert_not_awaited()
        assert (str(tmp_path), "retired", "D1") not in langchain._history
