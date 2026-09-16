"""Legacy OTA capabilities exercised through the actual aiohttp auth boundary."""

import asyncio
import logging
import time
from urllib.parse import urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

import firmware_delivery as delivery
from http_server import make_http_app
from tests.auth_helpers import TRANSPORT_HEADERS, owner_headers
from tests.test_authz_transports import isolated  # noqa: F401


@pytest.fixture
async def client(tmp_path):
    root = tmp_path / "firmware"
    root.mkdir()
    (root / "voice.bin").write_bytes(b"ESPFW" * 30000)
    (root / "other.bin").write_bytes(b"OTHER")
    # Match server.main: bearer URL paths must not enter aiohttp access logs.
    server = TestServer(make_http_app())
    await server.start_server(access_log=None)
    async with TestClient(server) as client:
        yield client


async def mint(client, name="voice.bin"):
    response = await client.post("/api/firmware-delivery/" + name, headers=owner_headers(client))
    assert response.status == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = await response.json()
    assert body["expires_in"] == 120
    url = urlsplit(body["url"])
    assert url.scheme == "https" and url.netloc == "owner.example"
    assert not url.query and not url.fragment
    return url.path


async def missing(response):
    assert response.status == 404
    assert await response.json() == {"error": "not found"}
    assert response.headers["Cache-Control"] == "no-store"


async def test_delivery_without_credentials_and_replay(client, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    # A compressed sibling must never replace the authorized binary.
    (tmp_path / "firmware" / "voice.bin.gz").write_bytes(b"WRONG")
    path = await mint(client)
    token = path.split("/")[2]
    assert len(token) == 43
    assert token not in repr(client.app[delivery.DELIVERIES])
    response = await client.get(path, allow_redirects=False)
    assert response.status == 200 and not response.history
    assert response.content_type == "application/octet-stream"
    assert response.headers["Content-Length"] == "150000"
    assert response.headers["Cache-Control"] == "no-store"
    assert await response.read() == b"ESPFW" * 30000
    await missing(await client.get(path))
    assert token not in caplog.text


async def test_expiry_at_deadline_and_wall_clock_independence(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(delivery, "monotonic", lambda: now[0])
    path = await mint(client)
    now[0] += 120
    await missing(await client.get(path))
    path = await mint(client)
    monkeypatch.setattr(time, "time", lambda: -1000000)
    now[0] += 119
    response = await client.get(path)
    assert response.status == 200
    await response.read()


@pytest.mark.parametrize("name", ["other.bin", "missing.bin", "secret.txt", "..%2Fvoice.bin", "%2Fvoice.bin"])
async def test_mismatch_and_traversal_are_indistinguishable(client, name):
    path = await mint(client)
    wrong = path.rsplit("/", 1)[0] + "/" + name
    await missing(await client.get(URL(wrong, encoded=True)))
    await missing(await client.get(path))


async def test_missing_and_unknown_tokens(client, tmp_path):
    path = await mint(client)
    (tmp_path / "firmware" / "voice.bin").unlink()
    await missing(await client.get(path))
    (tmp_path / "firmware" / "voice.bin").write_bytes(b"NEW")
    await missing(await client.get(path))
    await missing(await client.get("/firmware-delivery/unknown/voice.bin"))
    await missing(await client.get("/firmware-delivery/unknown/missing.bin"))


async def test_concurrent_redemption_has_one_winner(client):
    path = await mint(client)
    responses = await asyncio.gather(client.get(path), client.get(path))
    assert sorted(r.status for r in responses) == [200, 404]
    for response in responses:
        await response.read()


@pytest.mark.parametrize("role,status", [(None, 401), ("device", 403), ("integration", 403), ("owner", 401)])
async def test_only_owner_session_can_mint(client, role, status):
    headers = {**TRANSPORT_HEADERS}
    if role:
        headers["Authorization"] = f"Bearer {role}-secret"
    for name in ("voice.bin", "missing.bin"):
        response = await client.post("/api/firmware-delivery/" + name, headers=headers)
        assert response.status == status
    assert not client.app[delivery.DELIVERIES]


async def test_mint_requires_csrf_and_origin(client):
    for field in ("Origin", "X-CSRF-Token"):
        headers = owner_headers(client)
        del headers[field]
        response = await client.post("/api/firmware-delivery/voice.bin", headers=headers)
        assert response.status == 403
    assert not client.app[delivery.DELIVERIES]


@pytest.mark.parametrize("name", ["missing.bin", "secret.txt", "..%2Fvoice.bin", "%2Fvoice.bin", "folder.bin", "link.bin"])
async def test_mint_only_existing_regular_bin(client, tmp_path, name):
    root = tmp_path / "firmware"
    (root / "secret.txt").write_bytes(b"SECRET")
    (root / "folder.bin").mkdir()
    (root / "link.bin").symlink_to(root / "voice.bin")
    response = await client.post(URL("/api/firmware-delivery/" + name, encoded=True),
                                 headers=owner_headers(client))
    await missing(response)
    assert not client.app[delivery.DELIVERIES]


async def test_download_refuses_replaced_symlink(client, tmp_path):
    path = await mint(client)
    file = tmp_path / "firmware" / "voice.bin"
    file.unlink()
    file.symlink_to(tmp_path / "firmware" / "other.bin")
    await missing(await client.get(path))


async def test_capacity_and_expired_cleanup(client, monkeypatch):
    monkeypatch.setattr(delivery, "MAX_TOKENS", 2)
    now = [1000.0]
    monkeypatch.setattr(delivery, "monotonic", lambda: now[0])
    first, second = await mint(client), await mint(client)
    assert first != second
    response = await client.post("/api/firmware-delivery/voice.bin", headers=owner_headers(client))
    assert response.status == 503
    assert len(client.app[delivery.DELIVERIES]) == 2
    now[0] += 120
    await mint(client)
    assert len(client.app[delivery.DELIVERIES]) == 1
    await missing(await client.get(first))


async def test_original_route_still_requires_auth_and_url_token_is_not_general_auth(client):
    path = await mint(client)
    token = path.split("/")[2]
    for headers, query in [({}, ""), ({}, f"?token={token}"), ({"Authorization": f"Bearer {token}"}, "")]:
        response = await client.get("/firmware/voice.bin" + query, headers=headers)
        assert response.status == 401
    response = await client.get("/firmware/voice.bin", headers={"Authorization": "Bearer device-secret"})
    assert response.status == 200
    await response.read()


async def test_head_cannot_redeem_and_restart_invalidates(client):
    path = await mint(client)
    response = await client.head(path)
    assert response.status != 200
    assert len(client.app[delivery.DELIVERIES]) == 1
    server = TestServer(make_http_app())
    await server.start_server(access_log=None)
    async with TestClient(server) as other:
        await missing(await other.get(path))
    response = await client.get(path)
    assert response.status == 200
    await response.read()


async def test_malformed_delivery_paths_never_fall_back_to_spa(client, tmp_path, monkeypatch):
    import http_server

    (tmp_path / "index.html").write_text("SPA")
    monkeypatch.setattr(http_server, "WEB_CLIENT_DIST", str(tmp_path))
    for path in ("/firmware-delivery", "/firmware-delivery/", "/firmware-delivery/voice.bin",
                 "/firmware-delivery/token/nested/voice.bin"):
        await missing(await client.get(path))
    path = await mint(client)
    assert (await client.head(path)).status == 404
    response = await client.get(path)
    assert response.status == 200
    await response.read()
