"""Independent Support installation and real local runtime transport."""
from pathlib import Path

import pytest
from aiohttp import web

from vauxr.agents import registry, support
from vauxr.config import reset_config
from vauxr.config_files import update_json


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    reset_config()
    registry._reset_for_tests()
    yield
    reset_config()
    registry._reset_for_tests()


def test_builtin_install_is_idempotent_and_preserves_existing_route(tmp_path):
    registry.load()
    registry.register(registry.Agent("example", "Household", "openclaw", False, "created"))
    registry.activate("example")
    registry.ensure_support()
    first = (tmp_path / "agents.json").read_bytes()
    registry.ensure_support()
    assert first == (tmp_path / "agents.json").read_bytes()
    assert registry.get_active().id == "example"
    assert registry.get_by_id("vauxr-support").builtin is True
    assert registry.remove("vauxr-support") is False
    registry.load()
    assert registry.activate("vauxr-support")
    assert registry.get_active().type == "langchain"


async def test_unconfigured_is_honest_without_model_or_openclaw():
    registry.ensure_support()
    assert registry.get_active().id == "vauxr-support"
    assert support.settings() == support.EMPTY
    assert "needs setup" in await support.answer("browser", "Help")


@pytest.mark.parametrize("change", [
    {"endpoint": "https://example.com:443"}, {"endpoint": "http://localhost:11434"},
    {"endpoint": "http://127.0.0.1:11434/path"}, {"endpoint": "http://secret@127.0.0.1:11434"},
    {"model": "remote:cloud"}, {"provider": "paid-api"},
])
def test_local_only_configuration(change):
    with pytest.raises(ValueError):
        support.validate({"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "local", **change})


async def test_real_local_runtime_roundtrip_and_cloud_model_rejection(tmp_path, unused_tcp_port):
    remote = False
    calls = []

    async def show(request):
        assert await request.json() == {"model": "installed-local-model"}
        return web.json_response({"remote_host": "cloud.example"} if remote else {"details": {}})

    async def chat(request):
        calls.append(await request.json())
        return web.json_response({"done": True, "message": {"content": "Local support answer"}})

    app = web.Application()
    app.router.add_post("/api/show", show)
    app.router.add_post("/api/chat", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    update_json(Path(tmp_path) / "support.json", lambda _: {
        "provider": "ollama", "endpoint": f"http://127.0.0.1:{unused_tcp_port}", "model": "installed-local-model",
    })
    try:
        assert await support.answer("browser", "Explain voice modes") == "Local support answer"
        assert calls[0]["stream"] is False
        assert calls[0]["messages"][1] == {"role": "user", "content": "Explain voice modes"}
        assert "advice only" in calls[0]["messages"][0]["content"]
        remote = True
        assert "unavailable" in await support.answer("browser", "Do not send to cloud")
        assert len(calls) == 1
    finally:
        await runner.cleanup()


async def test_owner_cookie_and_csrf_required_for_runtime_configuration(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from tests.auth_helpers import owner_headers
    from vauxr.web.server import make_http_app

    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    reset_config()
    async with TestClient(TestServer(make_http_app())) as client:
        assert (await client.get("/api/support")).status == 401
        assert (await client.get("/api/support", headers={"Authorization": "Bearer integration-secret"})).status == 401
        headers = owner_headers(client)
        response = await client.get("/api/support", headers=headers)
        assert response.status == 200
        assert (await response.json())["status"] == "setup_needed"
        body = {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "local"}
        no_csrf = {k: v for k, v in headers.items() if k.lower() != "x-csrf-token"}
        assert (await client.post("/api/support", headers=no_csrf, json=body)).status == 403
        assert (await client.post("/api/support", headers=headers, json=body)).status == 200
        assert support.settings() == body
