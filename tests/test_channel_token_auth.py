"""Regression tests for channel-token bearer auth.

Covers two paths that the OpenClaw plugin (and other channel clients)
exercise against the HTTP API:

1. Malformed / oversize bearer tokens must yield a clean 401 rather than
   crashing the auth middleware. `bcrypt.checkpw` raises `ValueError` on
   inputs longer than 72 bytes or on a malformed stored hash; without
   defensive handling those exceptions surface as a 500 from any
   `@_require_auth` endpoint (including `POST /api/channels/{id}/rotate`).

2. Channels migrated from the old Node/bcryptjs server (which writes
   `$2a$` hashes) must continue to authenticate, and rotate, against the
   Python implementation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import bcrypt
import pytest
from aiohttp.test_utils import TestClient, TestServer

import channel_registry
import config as cfg_mod
from http_server import make_http_app


DEVICE_TOKEN = "tok-auth"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", DEVICE_TOKEN)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    channel_registry._reset_for_tests()
    channel_registry.load()
    yield
    channel_registry._reset_for_tests()
    cfg_mod.reset_config()


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    app = make_http_app()
    server = TestServer(app)
    async with TestClient(server) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_node_style_channels(data_dir: Path, raw_token: str, channel_id: str = "node-ch") -> None:
    """Write channels.json the way the old Node/bcryptjs server would.

    bcryptjs emits `$2a$`-prefixed hashes; Python bcrypt verifies those
    fine, but only if the load path actually keeps the bytes intact. We
    swap our `$2b$` to `$2a$` to mimic the on-disk format users would have
    after upgrading from the Node server.
    """
    h_2b = bcrypt.hashpw(raw_token.encode("utf-8"), bcrypt.gensalt(10))
    h_2a = b"$2a" + h_2b[3:]
    payload = [
        {
            "id": channel_id,
            "name": "OpenClaw (migrated)",
            "type": "openclaw",
            "tokenHash": h_2a.decode("utf-8"),
            "active": True,
            "createdAt": "2025-12-01T00:00:00.000Z",
        }
    ]
    (data_dir / "channels.json").write_text(json.dumps(payload, indent=2))


# --- Bug 1 / Bug 2: malformed bearer tokens must not 500 ---


async def test_oversize_bearer_token_returns_401_not_500(client: TestClient) -> None:
    """Tokens longer than 72 bytes blow up `bcrypt.checkpw`; the auth
    layer has to translate that into a clean 401."""
    # bcrypt's password limit is 72 bytes — anything beyond raises ValueError.
    # An attacker (or a misconfigured client) can trip this trivially.
    oversize = "x" * 200
    res = await client.get("/api/channels", headers=_bearer(oversize))
    assert res.status == 401, await res.text()


async def test_oversize_bearer_token_on_rotate_returns_401_not_500(
    client: TestClient, tmp_path: Path
) -> None:
    """Same crash, surfaced through `POST /api/channels/{id}/rotate`.

    This is the literal "rotate returns 500" reproducer: a request with a
    >72-byte Bearer token hits the auth gate before reaching the rotate
    handler, and `validate_channel_token` raises -> aiohttp returns 500.
    """
    # Seed a channel so the rotate endpoint *could* succeed if the auth
    # gate accepted the call — that way we know the 500 is the auth bug
    # and not a routing/handler issue.
    channel, _ = await channel_registry.create("victim", "openclaw")

    oversize = "vx_ch_" + ("a" * 80)  # 86 bytes
    res = await client.post(
        f"/api/channels/{channel.id}/rotate", headers=_bearer(oversize)
    )
    assert res.status == 401, await res.text()


async def test_malformed_stored_hash_does_not_500_other_requests(
    client: TestClient,
) -> None:
    """If channels.json is hand-edited (or corrupted) so one entry has an
    invalid tokenHash, that must not poison every other bearer request."""
    # Manually inject a broken channel directly into the registry, then a
    # valid one. Iteration order means the broken hash is hit first.
    broken = channel_registry.Channel(
        id="broken",
        name="Broken",
        type="openclaw",
        tokenHash="not-a-real-bcrypt-hash",
        active=False,
        createdAt="2025-01-01T00:00:00.000Z",
    )
    channel_registry._channels.append(broken)
    good, good_token = await channel_registry.create("Good", "openclaw")

    res = await client.get("/api/channels", headers=_bearer(good_token))
    assert res.status == 200, await res.text()
    body = await res.json()
    ids = {c["id"] for c in body}
    assert good.id in ids


# --- Bug 2: Node-migrated channels stay compatible ---


async def test_node_migrated_channel_authenticates(
    client: TestClient, tmp_path: Path
) -> None:
    """A channel created by the old Node/bcryptjs server (with `$2a$`
    hash) must still authenticate after the Python rewrite reads
    channels.json at startup."""
    raw_token = "vx_ch_" + "0123456789abcdef" * 4  # canonical 70-byte token
    _write_node_style_channels(tmp_path, raw_token, channel_id="node-ch")
    # Re-load now that channels.json is on disk.
    channel_registry._reset_for_tests()
    channel_registry.load()
    assert len(channel_registry._channels) == 1

    res = await client.get("/api/channels", headers=_bearer(raw_token))
    assert res.status == 200, await res.text()
    body = await res.json()
    assert any(c["id"] == "node-ch" for c in body)


async def test_node_migrated_channel_can_be_rotated(
    client: TestClient, tmp_path: Path
) -> None:
    """`POST /api/channels/{id}/rotate` against a Node-migrated channel,
    authenticated with the admin device token, must return a fresh
    `vx_ch_…` token and persist the new hash."""
    raw_token = "vx_ch_" + "fedcba9876543210" * 4
    _write_node_style_channels(tmp_path, raw_token, channel_id="node-ch")
    channel_registry._reset_for_tests()
    channel_registry.load()

    res = await client.post(
        "/api/channels/node-ch/rotate", headers=_bearer(DEVICE_TOKEN)
    )
    assert res.status == 200, await res.text()
    body = await res.json()
    assert body["token"].startswith("vx_ch_")
    assert body["token"] != raw_token
    # New token authenticates; old token does not.
    res_new = await client.get("/api/channels", headers=_bearer(body["token"]))
    assert res_new.status == 200
    res_old = await client.get("/api/channels", headers=_bearer(raw_token))
    assert res_old.status == 401
