"""Phase 12: agent_registry — bcrypt-backed agents + virtual openclaw-direct."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import agent_registry as cr
import config as cfg_mod


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.setenv("OPENCLAW_TOKEN", "")
    cr._reset_for_tests()
    cr.load()
    yield
    cr._reset_for_tests()
    cfg_mod.reset_config()


@pytest.mark.asyncio
async def test_create_returns_token_and_stores_hash() -> None:
    agent, token = await cr.create("Test Agent", "openclaw")
    assert agent.name == "Test Agent"
    assert agent.type == "openclaw"
    assert agent.active is False
    assert agent.id
    assert agent.createdAt
    # Public agent doesn't expose tokenHash.
    assert not hasattr(agent, "tokenHash")
    # Stored agent has it.
    full = cr.get_by_id(agent.id)
    assert full is not None
    assert full.tokenHash and full.tokenHash != token


@pytest.mark.asyncio
async def test_token_format() -> None:
    _, token = await cr.create("Test", "openclaw")
    assert re.match(r"^vx_ag_[0-9a-f]{64}$", token)


@pytest.mark.asyncio
async def test_list_omits_token_hash() -> None:
    await cr.create("Agent A", "openclaw")
    listed = cr.get_all()
    assert len(listed) == 1
    assert listed[0].name == "Agent A"
    assert not hasattr(listed[0], "tokenHash")


def test_list_includes_openclaw_direct_when_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_URL", "wss://test:18789")
    cfg_mod.reset_config()
    cr._reset_for_tests()
    cr.load()
    listed = cr.get_all()
    direct = next((c for c in listed if c.id == "openclaw-direct"), None)
    assert direct is not None
    assert direct.type == "openclaw-direct"
    assert direct.builtin is True


def test_list_omits_openclaw_direct_when_url_not_set() -> None:
    listed = cr.get_all()
    assert not any(c.id == "openclaw-direct" for c in listed)


@pytest.mark.asyncio
async def test_activate_deactivates_previous() -> None:
    a, _ = await cr.create("A", "openclaw")
    b, _ = await cr.create("B", "openclaw")

    cr.activate(a.id)
    assert cr.get_by_id(a.id).active is True  # type: ignore[union-attr]
    assert cr.get_by_id(b.id).active is False  # type: ignore[union-attr]

    cr.activate(b.id)
    assert cr.get_by_id(a.id).active is False  # type: ignore[union-attr]
    assert cr.get_by_id(b.id).active is True  # type: ignore[union-attr]


def test_activate_openclaw_direct_without_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_URL", "wss://test:18789")
    cfg_mod.reset_config()
    cr._reset_for_tests()
    cr.load()
    assert cr.activate("openclaw-direct") is True
    active = cr.get_active()
    assert active is not None
    assert active.id == "openclaw-direct"


@pytest.mark.asyncio
async def test_activating_agent_deactivates_openclaw_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_URL", "wss://test:18789")
    cfg_mod.reset_config()
    cr._reset_for_tests()
    cr.load()
    cr.activate("openclaw-direct")
    assert cr.get_active().id == "openclaw-direct"  # type: ignore[union-attr]

    ch, _ = await cr.create("My Agent", "openclaw")
    cr.activate(ch.id)
    assert cr.get_active().id == ch.id  # type: ignore[union-attr]
    direct = cr.get_by_id("openclaw-direct")
    assert direct is not None and direct.active is False


@pytest.mark.asyncio
async def test_delete() -> None:
    ch, _ = await cr.create("Doomed", "openclaw")
    assert len(cr.get_all()) == 1
    assert cr.remove(ch.id) is True
    assert cr.get_all() == []


def test_delete_nonexistent_returns_false() -> None:
    assert cr.remove("nope") is False


def test_delete_builtin_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_URL", "wss://test:18789")
    cfg_mod.reset_config()
    cr._reset_for_tests()
    cr.load()
    assert cr.remove("openclaw-direct") is False
    assert cr.get_by_id("openclaw-direct") is not None


@pytest.mark.asyncio
async def test_rotate_token() -> None:
    ch, old = await cr.create("Rotate", "openclaw")
    assert (await cr.validate_agent_token(old)) is not None

    new = await cr.rotate_token(ch.id)
    assert new is not None and re.match(r"^vx_ag_[0-9a-f]{64}$", new)
    assert new != old

    assert (await cr.validate_agent_token(new)) is not None
    assert (await cr.validate_agent_token(old)) is None


@pytest.mark.asyncio
async def test_rotate_builtin_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_URL", "wss://test:18789")
    cfg_mod.reset_config()
    cr._reset_for_tests()
    cr.load()
    assert (await cr.rotate_token("openclaw-direct")) is None


@pytest.mark.asyncio
async def test_load_save_roundtrip() -> None:
    ch, token = await cr.create("Persistent", "openclaw")
    cr.activate(ch.id)

    cr._reset_for_tests()
    cr.load()
    listed = cr.get_all()
    assert len(listed) == 1
    assert listed[0].name == "Persistent"
    assert listed[0].active is True

    valid = await cr.validate_agent_token(token)
    assert valid is not None and valid.id == ch.id
