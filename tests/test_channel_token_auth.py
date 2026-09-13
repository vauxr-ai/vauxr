"""Legacy channel hashes remain routing data, never authorization authority.

Malformed/oversize bearer values fail without bcrypt exceptions. Node-era
credentials and shared DEVICE_TOKEN cannot authenticate or rotate credentials;
explicit reenrollment is required by the breaking authorization contract.
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
    assert res.status == 401, await res.text()


# --- Legacy persistence never supplies transport authority ---


async def test_node_migrated_channel_requires_reenrollment(
    client: TestClient, tmp_path: Path
) -> None:
    """A channel created by the old Node/bcryptjs server (with `$2a$`
    hash) must not authenticate after the Python rewrite reads
    channels.json at startup."""
    raw_token = "vx_ch_" + "0123456789abcdef" * 4  # canonical 70-byte token
    _write_node_style_channels(tmp_path, raw_token, channel_id="node-ch")
    # Re-load now that channels.json is on disk.
    channel_registry._reset_for_tests()
    channel_registry.load()
    assert len(channel_registry._channels) == 1

    res = await client.get("/api/channels", headers=_bearer(raw_token))
    assert res.status == 401, await res.text()


async def test_legacy_device_token_cannot_rotate(
    client: TestClient, tmp_path: Path
) -> None:
    """`POST /api/channels/{id}/rotate` against a Node-migrated channel,
    authenticated with the admin device token, must fail closed without issuing any token."""
    raw_token = "vx_ch_" + "fedcba9876543210" * 4
    _write_node_style_channels(tmp_path, raw_token, channel_id="node-ch")
    channel_registry._reset_for_tests()
    channel_registry.load()

    res = await client.post(
        "/api/channels/node-ch/rotate", headers=_bearer(DEVICE_TOKEN)
    )
    assert res.status == 401, await res.text()
