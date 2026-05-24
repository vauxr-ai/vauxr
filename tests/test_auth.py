"""Phase 2: auth token check."""

from __future__ import annotations

import pytest

import auth
import config as cfg_mod


@pytest.fixture(autouse=True)
def _set_token(monkeypatch: pytest.MonkeyPatch):
    cfg_mod.reset_config()
    monkeypatch.setenv("DEVICE_TOKEN", "secret-device-token")
    yield
    cfg_mod.reset_config()


def test_validate_token_ok() -> None:
    res = auth.validate_token("secret-device-token")
    assert res.ok is True
    assert res.reason is None


def test_validate_token_wrong() -> None:
    res = auth.validate_token("wrong-token-same-length-xx")
    assert res.ok is False
    assert res.reason == "Invalid token"


def test_validate_token_length_mismatch() -> None:
    res = auth.validate_token("short")
    assert res.ok is False
    assert res.reason == "Invalid token"


def test_validate_token_empty() -> None:
    res = auth.validate_token("")
    assert res.ok is False
