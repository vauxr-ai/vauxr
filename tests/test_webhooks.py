"""Global webhook registry."""

from __future__ import annotations

from pathlib import Path

import pytest

import config as cfg_mod
import webhooks


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    webhooks.reset_for_tests()
    webhooks.load()
    yield
    webhooks.reset_for_tests()
    cfg_mod.reset_config()


def test_create_and_list_round_trip() -> None:
    hook = webhooks.create("HA", "http://ha.local:8123/api/events/x", "Bearer secret")
    assert hook.id.startswith("wh_")
    listed = webhooks.get_all()
    assert len(listed) == 1
    assert listed[0].name == "HA"
    assert listed[0].authorization == "Bearer secret"
    public = webhooks.public_dict(listed[0])
    assert "authorization" not in public
    assert public["has_authorization"] is True


def test_validate_url_rejects_non_http() -> None:
    assert webhooks.validate_url("ftp://nope") is not None
    assert webhooks.validate_url("http://ok.example/hook") is None


def test_update_and_delete(tmp_path: Path) -> None:
    hook = webhooks.create("A", "http://a.example/h")
    updated = webhooks.update(hook.id, name="B", authorization="Bearer x")
    assert updated is not None
    assert updated.name == "B"
    assert updated.authorization == "Bearer x"
    # Omit authorization → keep existing
    webhooks.update(hook.id, name="C")
    assert webhooks.get(hook.id).authorization == "Bearer x"  # type: ignore[union-attr]
    assert webhooks.remove(hook.id) is True
    assert webhooks.get(hook.id) is None
    assert (tmp_path / "webhooks.json").exists()


def test_load_from_disk(tmp_path: Path) -> None:
    webhooks.create("Kitchen", "https://example.com/hook")
    webhooks.reset_for_tests()
    webhooks.load()
    listed = webhooks.get_all()
    assert len(listed) == 1
    assert listed[0].name == "Kitchen"


# --- HTTP API ---


from collections.abc import AsyncIterator

from aiohttp.test_utils import TestClient, TestServer

from http_server import make_http_app


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    app = make_http_app()
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer tok"}


async def test_http_create_list_delete_webhook(client: TestClient) -> None:
    res = await client.post(
        "/api/webhooks",
        headers=_auth(),
        json={"name": "HA", "url": "http://ha.local/hook", "authorization": "Bearer s"},
    )
    assert res.status == 201
    body = await res.json()
    assert body["name"] == "HA"
    assert body["has_authorization"] is True
    assert "authorization" not in body
    wid = body["id"]

    listed = await (await client.get("/api/webhooks", headers=_auth())).json()
    assert len(listed) == 1
    assert listed[0]["id"] == wid

    res = await client.delete(f"/api/webhooks/{wid}", headers=_auth())
    assert res.status == 200
    listed = await (await client.get("/api/webhooks", headers=_auth())).json()
    assert listed == []


async def test_http_create_webhook_rejects_bad_url(client: TestClient) -> None:
    res = await client.post(
        "/api/webhooks",
        headers=_auth(),
        json={"name": "x", "url": "not-a-url"},
    )
    assert res.status == 400
