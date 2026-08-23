"""Phase 5: device_registry — register/unregister, seq, abort, config bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import config as cfg_mod
import device_registry as reg
from device_config import save_device_configs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "tok")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reg.reset()
    yield
    reg.reset()
    cfg_mod.reset_config()


def test_register_creates_entry() -> None:
    e = reg.register("dev1", ws=object(), name="Kitchen")
    assert e.id == "dev1"
    assert e.name == "Kitchen"
    assert e.state == "idle"
    assert reg.get("dev1") is e
    assert reg.get_all() == [e]


def test_register_name_falls_back_to_config_then_id(tmp_path: Path) -> None:
    save_device_configs(str(tmp_path), {"dev1": {"name": "FromDisk"}})  # type: ignore[arg-type]
    e1 = reg.register("dev1", ws=object())
    assert e1.name == "FromDisk"

    e2 = reg.register("dev2", ws=object())
    assert e2.name == "dev2"  # last-ditch fallback


def test_unregister_removes() -> None:
    reg.register("dev1", ws=object())
    reg.unregister("dev1")
    assert reg.get("dev1") is None


def test_set_state_updates_lastseen() -> None:
    e = reg.register("dev1", ws=object())
    prev = e.last_seen
    # Force a different timestamp.
    import time

    time.sleep(0.001)
    reg.set_state("dev1", "listening")
    assert e.state == "listening"
    assert e.last_seen >= prev


def test_next_seq_wraps_at_16_bits() -> None:
    e = reg.register("dev1", ws=object())
    e.seq = 0xFFFF
    assert reg.next_seq("dev1") == 0  # wraps
    assert reg.next_seq("dev1") == 1


def test_next_seq_unknown_device_returns_zero() -> None:
    assert reg.next_seq("does-not-exist") == 0


@pytest.mark.asyncio
async def test_abort_active_turn_sets_event() -> None:
    e = reg.register("dev1", ws=object())
    e.abort_event = asyncio.Event()
    reg.abort_active_turn("dev1")
    assert e.abort_event is None  # cleared


@pytest.mark.asyncio
async def test_abort_active_turn_signals_waiters() -> None:
    e = reg.register("dev1", ws=object())
    ev = asyncio.Event()
    e.abort_event = ev
    waiter = asyncio.create_task(ev.wait())
    reg.abort_active_turn("dev1")
    await asyncio.wait_for(waiter, timeout=0.1)


def test_update_config_merges_and_persists(tmp_path: Path) -> None:
    save_device_configs(str(tmp_path), {"dev1": {"name": "Old", "voice": True}})  # type: ignore[arg-type]
    reg.load_configs()
    reg.register("dev1", ws=object())
    merged = reg.update_config("dev1", {"name": "New"})  # type: ignore[arg-type]
    assert merged == {"name": "New", "voice": True}

    # Round-trip via fresh reload.
    reg.reset()
    from device_config import load_device_configs

    assert load_device_configs(str(tmp_path)) == {"dev1": {"name": "New", "voice": True}}


def test_get_config_for_returns_empty_when_unknown(tmp_path: Path) -> None:
    assert reg.get_config_for("never-seen") == {}


def test_register_aborts_prior_turn() -> None:
    e1 = reg.register("dev1", ws=object())
    ev = asyncio.Event()
    e1.abort_event = ev
    # Re-register the same device id.
    reg.register("dev1", ws=object())
    assert ev.is_set()


def test_register_preserves_hello_info() -> None:
    reg.register("dev1", ws=object())
    reg.set_hello_info("dev1", platform="satellite1", fw_version="abc123")
    e2 = reg.register("dev1", ws=object(), name="Kitchen")
    assert e2.platform == "satellite1"
    assert e2.fw_version == "abc123"
    assert e2.name == "Kitchen"
