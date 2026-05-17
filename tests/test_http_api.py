"""Phase 6: HTTP API skeleton — GET /api/devices + auth."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from aiohttp.test_utils import TestClient, TestServer

from vauxr import config as cfg_mod, device_registry as registry
from vauxr.http_server import make_http_app


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "http-test-token")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    registry.reset()
    yield
    registry.reset()
    cfg_mod.reset_config()


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    app = make_http_app()
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


async def test_devices_requires_auth(client: TestClient) -> None:
    res = await client.get("/api/devices")
    assert res.status == 401
    body = await res.json()
    assert body == {"error": "unauthorized"}


async def test_devices_wrong_bearer(client: TestClient) -> None:
    res = await client.get("/api/devices", headers={"Authorization": "Bearer wrong-token"})
    assert res.status == 401


async def test_devices_empty(client: TestClient) -> None:
    res = await client.get(
        "/api/devices",
        headers={"Authorization": "Bearer http-test-token"},
    )
    assert res.status == 200
    body = await res.json()
    assert body == []


async def test_devices_lists_registered(client: TestClient) -> None:
    entry = registry.register("kitchen", ws=object(), name="Kitchen")
    entry.last_seen = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    entry.state = "listening"

    res = await client.get(
        "/api/devices",
        headers={"Authorization": "Bearer http-test-token"},
    )
    assert res.status == 200
    body = await res.json()
    assert body == [
        {
            "id": "kitchen",
            "name": "Kitchen",
            "state": "listening",
            "lastSeen": "2026-05-17T12:00:00Z",
            "config": {},
        }
    ]


async def test_cors_options_preflight(client: TestClient) -> None:
    res = await client.options(
        "/api/devices",
        headers={
            "Origin": "https://example.test",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status == 204
    assert res.headers["Access-Control-Allow-Origin"] == "*"
    assert "GET" in res.headers["Access-Control-Allow-Methods"]


async def test_cors_headers_on_responses(client: TestClient) -> None:
    res = await client.get(
        "/api/devices", headers={"Authorization": "Bearer http-test-token"}
    )
    assert res.headers["Access-Control-Allow-Origin"] == "*"
