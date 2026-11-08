"""Standalone graph, independent provider configuration and household delivery boundary."""
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import owner_headers
from vauxr.agents import registry, langgraph
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
    reset_config();registry._reset_for_tests();webhooks.reset_for_tests()
    yield
    reset_config();registry._reset_for_tests();webhooks.reset_for_tests()


async def test_owner_local_agent_flow_never_mints_integration_credentials():
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.post("/api/local-agents", json={"framework":"langgraph","name":"Household"})).status == 401
        headers = owner_headers(client)
        response = await client.post("/api/local-agents", headers=headers, json={"framework":"langgraph","name":"Household"})
        assert response.status == 201
        agent = await response.json()
        assert agent["type"] == "langgraph" and agent["integration"] is False
        assert "token" not in json.dumps(agent).lower()
        assert "needs setup" in await langgraph.answer(agent["id"], "D1", "Help")
        from vauxr.speech.store import get_store
        store = get_store()
        before = store.path.read_bytes() if store.path.exists() else None
        with pytest.raises(ValueError, match="does not support"):
            store.update({"enabled_modes": ["standard", "realtime"], "default_mode": "standard"}, agent_id=agent["id"])
        assert (store.path.read_bytes() if store.path.exists() else None) == before
        path = f'/api/local-agents/{agent["id"]}/runtime' 
        assert (await (await client.get(path, headers=headers)).json())["status"] == "setup_needed"
        assert (await client.post(path, headers=headers, json={"provider":"ollama","endpoint":"http://127.0.0.1:11434","model":"local"})).status == 200
        registry.activate(agent["id"])
        assert (await client.delete(f'/api/local-agents/{agent["id"]}',headers=headers)).status == 409
        registry.ensure_support();registry.activate("vauxr-support")
        assert (await client.delete(f'/api/local-agents/{agent["id"]}',headers=headers)).status == 200


async def test_real_graph_routes_through_shared_agent_transport_and_named_household_action(tmp_path, unused_tcp_port):
    model_requests=[];deliveries=[]
    async def show(_):return web.json_response({"details":{}})
    async def model(request):
        body=await request.json();model_requests.append(body)
        assert "household-secret" not in json.dumps(body)
        assert "/household-endpoint" not in json.dumps(body)
        n=len(model_requests)
        if n==1:message={"role":"assistant","tool_calls":[{"function":{"name":"vauxr_household_list","arguments":{}}}]}
        elif n==2:
            listing=json.loads(body["messages"][-1]["content"])
            assert listing=={"actions":[{"id":hook.id,"name":"Household lights"}]}
            message={"role":"assistant","tool_calls":[{"function":{"name":"vauxr_household_action","arguments":{"webhook_id":hook.id}}}]}
        elif n==3:
            message={"role":"assistant","tool_calls":[{"function":{"name":"vauxr_household_action","arguments":{"webhook_id":hook.id}}}]}
        else:
            outcome=json.loads(body["messages"][-1]["content"])
            assert outcome=={"delivered":True,"http_status":200,"external_state_verified":False,"reused_result":True}
            message={"role":"assistant","content":"The household request was delivered; external state is not verified."}
        return web.json_response({"done":True,"message":message})
    async def household(request):
        assert request.headers["Authorization"]=="Bearer household-secret"
        deliveries.append(await request.json())
        return web.json_response({"private_response":"household-secret"})
    app=web.Application();app.router.add_post("/api/show",show);app.router.add_post("/api/chat",model);app.router.add_post("/household-endpoint",household)
    runner=web.AppRunner(app);await runner.setup();await web.TCPSite(runner,"127.0.0.1",unused_tcp_port).start()
    hook=webhooks.create("Household lights",f"http://127.0.0.1:{unused_tcp_port}/household-endpoint","Bearer household-secret",{"entity_id":"light.fixture"})
    agent=registry.Agent("a"*32,"Graph household","langgraph",False,"created");registry.register(agent);registry.activate(agent.id)
    update_json(Path(tmp_path)/"local-agent-models.json",lambda _:{agent.id:{"provider":"ollama","endpoint":f"http://127.0.0.1:{unused_tcp_port}","model":"installed-local"}})
    assert not (Path(tmp_path)/"support.json").exists()
    server=AgentServer();done=asyncio.get_running_loop().create_future();chunks=[]
    listener={"on_delta":lambda _id,text:chunks.append(text),"on_end":lambda _id:done.set_result(True),"on_error":lambda _id,error:done.set_exception(RuntimeError(error))}
    server.add_response_listener("D1",listener)
    try:
        assert server.send_transcript("D1","Use the household lights action",listener=listener)
        await asyncio.wait_for(done,10)
        assert "request was delivered" in "".join(chunks)
        assert len(model_requests)==4 and deliveries==[{"entity_id":"light.fixture"}]
    finally:
        server.remove_response_listener("D1",listener);await runner.cleanup()
