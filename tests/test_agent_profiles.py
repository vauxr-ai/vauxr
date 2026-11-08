"""Persisted profile selection through owner APIs and the real Ollama transport."""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import owner_headers
from vauxr.agents import langchain, registry, support
from vauxr.config import reset_config
from vauxr.settings.providers import ProviderStore
from vauxr.web.server import make_http_app


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    reset_config()
    registry._reset_for_tests()
    registry.ensure_support()
    yield
    reset_config()
    registry._reset_for_tests()


async def test_profile_api_persistence_and_runtime_transport(tmp_path):
    calls = []

    async def show(request):
        calls.append(("show", await request.json()))
        return web.json_response({"details": {}})

    async def chat(request):
        calls.append(("chat", await request.json()))
        return web.json_response({"done": True, "message": {"content": "Profile transport answer"}})

    runtime = web.Application()
    runtime.router.add_post("/base/api/show", show)
    runtime.router.add_post("/base/api/chat", chat)
    async with TestServer(runtime) as transport, TestClient(TestServer(make_http_app())) as client:
        headers = owner_headers(client)
        body = {"name": "Local fixture", "provider": "ollama", "endpoint": str(transport.make_url('/base')),
                "model": "fixture-first", "auth": "none"}
        profile_path = "/api/settings/providers/profiles/local"
        assert (await client.put(profile_path, headers=headers, json=body)).status == 200
        agent = await (await client.post("/api/local-agents", headers=headers,
                                        json={"definition_id": "household", "name": "Household"})).json()
        path = f"/api/local-agents/{agent['id']}/runtime"
        no_csrf = {k: v for k, v in headers.items() if k.lower() != "x-csrf-token"}
        assert (await client.post(path, headers=no_csrf, json={"profile_id": "local"})).status == 403
        assert (await client.post(path, headers=headers, json={"profile_id": "missing"})).status == 400
        assert (await client.post(path, headers=headers,
                                  json={"profile_id": "local", "api_key": "must-not-save"})).status == 400
        result = await (await client.post(path, headers=headers, json={"profile_id": "local"})).json()
        assert result == {"profile_id": "local", "status": "configured"}
        assert json.loads((tmp_path / "local-agent-models.json").read_text()) == {
            agent["id"]: {"profile_id": "local"}}
        registry.load()
        assert registry.get_by_id(agent["id"]).name == "Household"
        assert await langchain.answer(agent["id"], "browser", "Hello") == "Profile transport answer"
        assert calls[-1][1]["model"] == "fixture-first"
        assert {t["function"]["name"] for t in calls[-1][1]["tools"]} == {
            "vauxr_household_list", "vauxr_household_action"}
        assert (await client.put(profile_path, headers=headers, json={**body, "model": "fixture-second"})).status == 200
        assert await langchain.answer(agent["id"], "browser", "Again") == "Profile transport answer"
        assert calls[-1][1]["model"] == "fixture-second"
        assert (await client.post("/api/support", headers=headers, json={"profile_id": "local"})).status == 200
        alias = await (await client.get("/api/local-agents/vauxr-support/runtime", headers=headers)).json()
        assert alias == {"profile_id": "local", "status": "configured"}
        assert await support.answer("support-browser", "Hi") == "Profile transport answer"
        assert "vauxr_household_action" in {t["function"]["name"] for t in calls[-1][1]["tools"]}
        # Direct configuration remains the same validated legacy contract.
        direct = {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "legacy"}
        assert (await client.post(path, headers=headers, json=direct)).status == 200
        assert await (await client.get(path, headers=headers)).json() == {**direct, "status": "configured"}


async def test_unsupported_and_missing_credentials_never_request_or_disclose(tmp_path, monkeypatch, caplog):
    store = ProviderStore(tmp_path)
    profile = {"name": "API fixture", "provider": "openai", "endpoint": "https://api.openai.com",
               "model": "fixture-model", "auth": "inline", "credential": "private-fixture-canary"}
    store.save("api", profile)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported profile must never create a model transport")

    monkeypatch.setattr(langchain.aiohttp, "ClientSession", forbidden)
    async with TestClient(TestServer(make_http_app())) as client:
        headers = owner_headers(client)
        for path in ["/api/support", "/api/local-agents/vauxr-support/runtime"]:
            response = await client.post(path, headers=headers, json={"profile_id": "api"})
            assert response.status == 200
            assert await response.json() == {"profile_id": "api", "status": "unsupported"}
            assert "private-fixture-canary" not in await (await client.get(path, headers=headers)).text()
        assert "unsupported" in await support.answer("browser", "Hello")
        store.save("api", {k: v for k, v in profile.items() if k != "credential"} | {"clear_credential": True})
        assert (await (await client.get("/api/support", headers=headers)).json())["status"] == "setup_needed"
        assert "needs setup" in await support.answer("browser", "Hello")
        assert "private-fixture-canary" not in caplog.text
