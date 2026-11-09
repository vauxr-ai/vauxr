"""Owner-authorized display-name changes never alter routing or credential identity."""
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import vauxr.agents.registry as agents
import vauxr.auth.service as auth
import vauxr.config as config
from tests.auth_helpers import TRANSPORT_HEADERS, owner_headers, seed
from vauxr.auth.policy import Role
from vauxr.web.server import make_http_app


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    config.reset_config()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.setenv("REALTIME_ENABLED", "0")
    auth._store = None
    agents._reset_for_tests()
    seed("integration-secret", Role.INTEGRATION, "route")
    seed("device-secret", Role.DEVICE, "device")
    agents.register(agents.Agent("route", "Original", "openclaw", False, "2026-11-08"))
    agents.activate("route")
    try:
        async with TestClient(TestServer(make_http_app())) as client:
            yield client
    finally:
        auth._store = None
        agents._reset_for_tests()
        config.reset_config()


async def test_rename_persists_without_identity_or_selection_changes(client):
    headers = owner_headers(client)
    credentials = tuple(auth.get_store().records)
    original = agents.get_by_id("route")
    response = await client.patch("/api/agents/route", headers=headers, json={"name": "  Kitchen helper  "})
    assert response.status == 200
    assert await response.json() == {"id": "route", "name": "Kitchen helper"}
    agents._reset_for_tests()
    renamed = agents.get_active()
    assert renamed is not None and original is not None
    assert renamed.name == "Kitchen helper"
    assert (renamed.id, renamed.type, renamed.createdAt, renamed.integration) == (
        original.id, original.type, original.createdAt, original.integration
    )
    assert tuple(auth.get_store().records) == credentials
    # Repeated enrollment registration must preserve the owner-chosen name.
    agents.register(original)
    assert agents.get_by_id("route").name == "Kitchen helper"


@pytest.mark.parametrize("body", [
    {}, {"name": ""}, {"name": "   "}, {"name": "x" * 201}, {"name": 3},
    {"name": "two\nlines"}, {"name": "Name", "id": "different"},
    {"name": "Name", "active": False}, {"name": "Name", "token": "forbidden"},
])
async def test_invalid_name_or_unrelated_fields_rejected(client, body):
    response = await client.patch("/api/agents/route", headers=owner_headers(client), json=body)
    assert response.status == 400
    assert agents.get_active().name == "Original"


async def test_rename_authorization_and_csrf(client):
    owner = owner_headers(client)
    for headers, status in [
        ({}, 401),
        ({**TRANSPORT_HEADERS, "Authorization": "Bearer integration-secret"}, 403),
        ({**TRANSPORT_HEADERS, "Authorization": "Bearer device-secret"}, 403),
        ({key: value for key, value in owner.items() if key != "X-CSRF-Token"}, 403),
        ({**owner, "Origin": "https://foreign.example"}, 403),
    ]:
        response = await client.patch("/api/agents/route", headers=headers, json={"name": "Denied"})
        assert response.status == status
    assert agents.get_active().name == "Original"
    response = await client.patch("/api/agents/missing", headers=owner, json={"name": "Valid"})
    assert response.status == 404


async def test_save_failure_is_actionable_and_does_not_claim_success(client, monkeypatch):
    def fail(*args):
        raise OSError("test private filesystem detail")

    headers = owner_headers(client)
    monkeypatch.setattr(agents, "update_json", fail)
    response = await client.patch("/api/agents/route", headers=headers, json={"name": "Unsaved"})
    assert response.status == 503
    body = await response.json()
    assert "Refresh before retrying" in body["error"]
    assert "private filesystem" not in body["error"]
    assert agents.get_active().name == "Original"


async def test_two_hundred_character_name_persists(client):
    name = "x" * 200
    response = await client.patch("/api/agents/route", headers=owner_headers(client), json={"name": name})
    assert response.status == 200
    agents._reset_for_tests()
    assert agents.get_by_id("route").name == name
