"""Phase 2: config + auth + utils."""

from __future__ import annotations

import pytest

import config as cfg_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    cfg_mod.reset_config()
    # The Node version requires DEVICE_TOKEN; default it for tests that
    # don't override.
    monkeypatch.setenv("DEVICE_TOKEN", "test-device-token")
    yield
    cfg_mod.reset_config()


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
    monkeypatch.delenv("WHISPER_URL", raising=False)
    monkeypatch.delenv("PIPER_URL", raising=False)
    monkeypatch.delenv("PIPER_VOICE", raising=False)
    monkeypatch.delenv("WS_PORT", raising=False)
    monkeypatch.delenv("HTTP_PORT", raising=False)
    monkeypatch.delenv("STREAMING_TTS_IDLE_PAUSE_MS", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    c = cfg_mod.load_config()
    assert c.openclaw.url == ""
    assert c.openclaw.token == ""
    assert c.channel.ws_path == "/channel"
    assert c.device.token == "test-device-token"
    assert c.data_dir == "/data"
    assert c.whisper.host == "whisper"
    assert c.whisper.port == 10300
    assert c.piper.host == "piper"
    assert c.piper.port == 10200
    assert c.piper.voice == "en_US-libritts_r-medium"
    assert c.ws.port == 8765
    assert c.http.port == 8080
    assert c.streaming_tts.idle_pause_ms == 400
    assert c.log_level == "info"


def test_load_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_URL", "wss://openclaw.example:18789")
    monkeypatch.setenv("OPENCLAW_TOKEN", "tok-abc")
    monkeypatch.setenv("WHISPER_URL", "tcp://whisper-host:9999")
    monkeypatch.setenv("PIPER_URL", "tcp://piper-host:1234")
    monkeypatch.setenv("PIPER_VOICE", "en_US-amy-medium")
    monkeypatch.setenv("WS_PORT", "9999")
    monkeypatch.setenv("HTTP_PORT", "9090")
    monkeypatch.setenv("STREAMING_TTS_IDLE_PAUSE_MS", "777")
    monkeypatch.setenv("DATA_DIR", "/var/lib/vauxr")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    c = cfg_mod.load_config()
    assert c.openclaw.url == "wss://openclaw.example:18789"
    assert c.openclaw.token == "tok-abc"
    assert c.whisper == cfg_mod.WyomingEndpoint("whisper-host", 9999)
    assert c.piper.host == "piper-host"
    assert c.piper.port == 1234
    assert c.piper.voice == "en_US-amy-medium"
    assert c.ws.port == 9999
    assert c.http.port == 9090
    assert c.streaming_tts.idle_pause_ms == 777
    assert c.data_dir == "/var/lib/vauxr"
    assert c.log_level == "debug"


def test_load_config_missing_device_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVICE_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="DEVICE_TOKEN"):
        cfg_mod.load_config()


def test_parse_wyoming_url_without_tcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Node parser strips a leading `tcp://` but otherwise accepts plain
    # `host:port`. Match that behavior.
    monkeypatch.setenv("WHISPER_URL", "plain:5555")
    c = cfg_mod.load_config()
    assert c.whisper == cfg_mod.WyomingEndpoint("plain", 5555)


def test_get_config_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    a = cfg_mod.get_config()
    monkeypatch.setenv("DEVICE_TOKEN", "different")
    b = cfg_mod.get_config()
    assert a is b  # cached
    cfg_mod.reset_config()
    c = cfg_mod.get_config()
    assert c is not a
