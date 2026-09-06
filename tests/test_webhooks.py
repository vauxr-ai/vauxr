"""Global webhook registry."""

from __future__ import annotations

import json
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
    assert public["body"] is None


def test_create_with_body_round_trip(tmp_path: Path) -> None:
    hook = webhooks.create(
        "Lights low",
        "http://ha.local:8123/api/services/scene/turn_on",
        "Bearer secret",
        {"entity_id": "scene.lights_low"},
    )
    assert hook.body == {"entity_id": "scene.lights_low"}
    public = webhooks.public_dict(hook)
    assert public["body"] == {"entity_id": "scene.lights_low"}
    webhooks.reset_for_tests()
    webhooks.load()
    loaded = webhooks.get(hook.id)
    assert loaded is not None
    assert loaded.body == {"entity_id": "scene.lights_low"}
    disk = json.loads((tmp_path / "webhooks.json").read_text())
    assert disk["webhooks"][0]["body"] == {"entity_id": "scene.lights_low"}


def test_parse_body_rejects_non_object() -> None:
    parsed, err = webhooks.parse_body("[1]")
    assert parsed is None
    assert err is not None
    parsed, err = webhooks.parse_body("")
    assert parsed is None
    assert err is None
    parsed, err = webhooks.parse_body({"entity_id": "scene.x"})
    assert parsed == {"entity_id": "scene.x"}
    assert err is None


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


def test_duplicate_copies_secret_but_public_omits_it(tmp_path: Path) -> None:
    hook = webhooks.create(
        "Lights low",
        "http://ha.local:8123/api/services/scene/turn_on",
        "Bearer secret",
        {"entity_id": "scene.lights_low"},
    )
    clone = webhooks.duplicate(hook.id)
    assert clone is not None
    assert clone.id != hook.id
    assert clone.name == "Lights low copy"
    assert clone.url == hook.url
    assert clone.authorization == "Bearer secret"
    assert clone.body == {"entity_id": "scene.lights_low"}
    assert clone.body is not hook.body
    public = webhooks.public_dict(clone)
    assert "authorization" not in public
    assert public["has_authorization"] is True
    disk = json.loads((tmp_path / "webhooks.json").read_text())
    by_id = {w["id"]: w for w in disk["webhooks"]}
    assert by_id[clone.id]["authorization"] == "Bearer secret"


def test_duplicate_unique_copy_names() -> None:
    hook = webhooks.create("HA", "http://ha.local/hook")
    first = webhooks.duplicate(hook.id)
    second = webhooks.duplicate(hook.id)
    third = webhooks.duplicate(hook.id)
    assert first is not None and second is not None and third is not None
    assert first.name == "HA copy"
    assert second.name == "HA copy 2"
    assert third.name == "HA copy 3"
    assert webhooks.duplicate("wh_missing") is None


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


async def test_http_create_webhook_with_body(client: TestClient) -> None:
    res = await client.post(
        "/api/webhooks",
        headers=_auth(),
        json={
            "name": "Lights low",
            "url": "http://ha.local:8123/api/services/scene/turn_on",
            "body": {"entity_id": "scene.lights_low"},
        },
    )
    assert res.status == 201
    body = await res.json()
    assert body["body"] == {"entity_id": "scene.lights_low"}

    res = await client.patch(
        f"/api/webhooks/{body['id']}",
        headers=_auth(),
        json={"body": None},
    )
    assert res.status == 200
    updated = await res.json()
    assert updated["body"] is None


async def test_http_create_webhook_rejects_bad_body(client: TestClient) -> None:
    res = await client.post(
        "/api/webhooks",
        headers=_auth(),
        json={"name": "x", "url": "http://ok.example/h", "body": ["nope"]},
    )
    assert res.status == 400


async def test_http_duplicate_webhook(client: TestClient, tmp_path: Path) -> None:
    res = await client.post(
        "/api/webhooks",
        headers=_auth(),
        json={
            "name": "HA",
            "url": "http://ha.local/hook",
            "authorization": "Bearer s",
            "body": {"entity_id": "scene.x"},
        },
    )
    assert res.status == 201
    src = await res.json()
    assert "authorization" not in src

    res = await client.post(f"/api/webhooks/{src['id']}/duplicate", headers=_auth())
    assert res.status == 201
    clone = await res.json()
    assert clone["name"] == "HA copy"
    assert clone["url"] == src["url"]
    assert clone["body"] == {"entity_id": "scene.x"}
    assert clone["has_authorization"] is True
    assert "authorization" not in clone
    assert clone["id"] != src["id"]

    disk = json.loads((tmp_path / "webhooks.json").read_text())
    by_id = {w["id"]: w for w in disk["webhooks"]}
    assert by_id[clone["id"]]["authorization"] == "Bearer s"


async def test_http_duplicate_webhook_404(client: TestClient) -> None:
    res = await client.post("/api/webhooks/wh_missing/duplicate", headers=_auth())
    assert res.status == 404
