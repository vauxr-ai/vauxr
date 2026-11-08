"""Bounded context through real owner HTTP and production plugin dispatch."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests import test_agent_voice
from tests.auth_helpers import owner_headers, seed
from vauxr import companion
from vauxr.agents import registry as agents
from vauxr.agents.server import AgentServer, _Connection
from vauxr.auth.policy import Role
from vauxr.auth.service import authenticate, get_store
from vauxr.speech.store import SpeechStore
from vauxr.web.server import make_http_app


@pytest.fixture
async def portal(tmp_path, monkeypatch):
    store, first, second = await test_agent_voice.voice.__wrapped__(tmp_path, monkeypatch)
    import vauxr.web.companion as http
    monkeypatch.setattr(http, "speech_store", lambda: store)
    for identity in ("B1", "B2", "physical"):
        seed(identity + "-secret", Role.DEVICE, identity)
    seed("agent-secret", Role.INTEGRATION, first.id)
    # Durable enrollment kind is required in addition to a valid device credential.
    get_store().lifecycle = {"version": 1, "operations": {}, "blocked": [], "bindings": {
        identity: {"kind": "physical" if identity == "physical" else "browser", "public_key": "ab" * 32}
        for identity in ("B1", "B2", "physical")}, "recovery": {}}
    get_store().replace(get_store().records)
    async with TestClient(TestServer(make_http_app())) as client:
        headers = {**owner_headers(client), "X-Vauxr-Browser": "B1-secret"}
        yield client, headers, store, first, second
    companion._views.clear()


def context(first, **extra):
    return {"browser_id": "B1", "route": "agents", "resource_type": "agent",
            "resource_id": first.id, "setting_key": "name", "current_value": first.name, **extra}


async def post(portal, action, body):
    client, headers, *_ = portal
    return await client.post("/api/companion/" + action, headers=headers, json=body)


async def test_existing_settings_rename_persists(portal):
    client, headers, _, first, _ = portal
    response = await client.patch(f"/api/agents/{first.id}", headers=headers, json={"name": "Changed"})
    assert response.status == 200
    agents._reset_for_tests()
    assert agents.get_by_id(first.id).name == "Changed"
    assert agents.get_active().id == first.id


@pytest.mark.parametrize("extra", [
    {"token": "private"}, {"url": "https://example/?secret=private"}, {"dom": "private"},
    {"resource_id": "vx_dev_private"}, {"setting_key": "token"},
    {"current_value": "private"}, {"route": "agents?token=private"},
    {"resource_type": {"token": "private"}}, {"revision": "private"},
])
async def test_strict_context_non_disclosure(portal, extra):
    response = await post(portal, "context", context(portal[3], **extra))
    assert response.status == 409
    assert "private" not in await response.text()
    assert companion.untrusted("B1") == ""


async def test_auth_owner_browser_kind_identity_csrf_and_cross_browser(portal):
    client, headers, _, first, _ = portal
    for patch, body, expected in [
        ({"Cookie": ""}, context(first), 401),
        ({"X-CSRF-Token": "wrong"}, context(first), 403),
        ({"X-Vauxr-Browser": ""}, context(first), 403),
        ({"X-Vauxr-Browser": "agent-secret"}, context(first), 403),
        ({"X-Vauxr-Browser": "physical-secret"}, context(first), 403),
        ({"X-Vauxr-Browser": "B2-secret"}, context(first), 409),
        ({"Origin": "https://elsewhere.example"}, context(first), 403),
    ]:
        response = await client.post("/api/companion/context", headers={**headers, **patch}, json=body)
        assert response.status == expected


async def test_selector_persistence_isolation_and_conflict(portal):
    _, _, store, first, second = portal
    store.record_capabilities("B1", ["ws", "webrtc"])
    assert (await post(portal, "select", {"browser_id": "B1", "agent_id": second.id})).status == 200
    restored = SpeechStore(store.path.parent, ())
    assert restored.assignments == {"B1": second.id}
    assert agents.get_for_device("physical").id == first.id
    assert agents.get_for_device("B2").id == first.id
    store.update({"enabled_modes": ["realtime"], "default_mode": "realtime"}, agent_id=first.id)
    store.update({"mode": "standard"}, "B1")
    assert (await post(portal, "select", {"browser_id": "B1", "agent_id": first.id})).status == 409
    assert store.assignments["B1"] == second.id


async def test_actual_standard_and_live_dispatch_context_without_credentials(portal):
    _, _, _, first, _ = portal
    assert (await post(portal, "context", context(first))).status == 200
    server = AgentServer()
    conn = _Connection(SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock()))
    conn.principal = authenticate("agent-secret")
    conn.agent = first
    server._connections[first.id] = conn
    assert server.send_transcript("B1", "Explain this setting")
    await asyncio.sleep(0)
    frame = json.loads(conn.ws.send_str.call_args.args[0])
    assert frame["deviceId"] == "B1"
    assert "Untrusted browser UI data" in frame["text"]
    assert '"current_value": "Household"' in frame["text"]
    assert "secret" not in frame["text"]
    assert "B1" not in frame["text"]
    assert server.send_transcript("physical", "hello")
    await asyncio.sleep(0)
    assert json.loads(conn.ws.send_str.call_args.args[0])["text"] == "hello"
    for operation in ("bootstrap", "consult"):
        assert (await post(portal, "context", context(first))).status == 200
        task = asyncio.create_task(server.realtime_request(first.id, "B1", "session", operation,
                                   {"request": "Explain"} if operation == "consult" else {}))
        await asyncio.sleep(0)
        frame = json.loads(conn.ws.send_str.call_args.args[0])
        if operation == "bootstrap":
            assert (await post(portal, "context", {"browser_id": "B1", "route": "settings",
                "resource_type": None, "resource_id": None, "setting_key": None, "current_value": None})).status == 200
        if operation == "consult":
            assert "Untrusted browser UI data" in frame["payload"]["request"]
        await server._handle_authenticated_message(conn, {"type": "agent.realtime.result",
            "requestId": frame["requestId"], "deviceId": "B2", "result": {"messages": []}})
        assert not task.done()
        await server._handle_authenticated_message(conn, {"type": "agent.realtime.result",
            "requestId": frame["requestId"], "deviceId": "B1", "result": {"messages": []}})
        result = await task
        if operation == "bootstrap":
            assert result["messages"][-1]["role"] == "user"
            assert "Untrusted browser UI data" in result["messages"][-1]["content"]
            assert '"route": "settings"' in result["messages"][-1]["content"]
    await server._handle_authenticated_message(conn, {"type": "agent.ui.name.update",
        "deviceId": "B1", "new_value": "Not executed"})
    assert json.loads(conn.ws.send_str.call_args.args[0])["error"] == "Name update unavailable"
    assert agents.get_by_id(first.id).name == "Household"
    # Credential retirement immediately removes context even if the UI stays open.
    get_store().replace(tuple(record for record in get_store().records if record.id != "B1-credential"))
    assert companion.untrusted("B1") == ""


async def test_duplicate_keys_queries_and_unknown_actions_are_denied(portal):
    client, headers, *_ = portal
    response = await client.post("/api/companion/context", headers=headers,
        data='{"browser_id":"B1","browser_id":"B1","token":"private"}')
    assert response.status == 409
    response = await client.post("/api/companion/context?token=private", headers=headers,
                                 json=context(portal[3]))
    assert response.status == 409
    assert "private" not in await response.text()
    response = await post(portal, "execute", {"browser_id": "B1"})
    assert response.status == 404


async def test_fragmented_context_body_and_oversized_denial(portal):
    client, headers, *_ = portal
    headers = {**headers, "Content-Type": "application/json"}
    raw = json.dumps(context(portal[3])).encode()

    async def fragments():
        yield raw[:30]
        await asyncio.sleep(0.01)
        yield raw[30:]

    response = await client.post("/api/companion/context", headers=headers, data=fragments())
    assert response.status == 200
    response = await client.post("/api/companion/context", headers=headers, data=b" " * 4097)
    assert response.status == 409


async def test_trusted_plugin_rename_needs_no_ui_lease_and_preserves_identity(portal):
    _, _, _, first, second = portal
    server = AgentServer()
    conn = _Connection(SimpleNamespace(closed=False, send_str=AsyncMock(), close=AsyncMock()))
    conn.principal = authenticate("agent-secret")
    conn.agent = first
    server._connections[first.id] = conn
    # No published view, transcript, selected target, revision or admission is needed.
    for value in ("Kitchen helper", "Kitchen helper updated"):
        await server._handle_authenticated_message(conn, {"type": "agent.ui.name.update",
            "deviceId": "B1", "requestId": value, "resourceId": second.id, "newValue": value})
        frame = json.loads(conn.ws.send_str.call_args.args[0])
        assert frame["requestId"] == value
        assert frame["result"] == {"resource_id": second.id, "name": value}
    agents._reset_for_tests()
    assert agents.get_by_id(second.id).name == "Kitchen helper updated"
    assert agents.get_active().id == first.id
    # Existing authenticated-connection identity checks remain enforced.
    conn.agent = second
    await server._handle_authenticated_message(conn, {"type": "agent.ui.name.update",
        "deviceId": "B1", "requestId": "mismatch", "resourceId": first.id, "newValue": "Wrong"})
    assert json.loads(conn.ws.send_str.call_args.args[0])["code"] == "FORBIDDEN"
    assert agents.get_by_id(first.id).name == "Household"
