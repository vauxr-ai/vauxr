"""Phase 2: config + auth + utils."""

from __future__ import annotations

import pytest

import config as cfg_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    cfg_mod.reset_config()
    for name in ("STT_URL", "TTS_URL", "TTS_VOICE"):
        monkeypatch.delenv(name, raising=False)
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
    assert c.stt.host == "whisper"
    assert c.stt.port == 10300
    assert c.tts.host == "piper"
    assert c.tts.port == 10200
    assert c.tts.voice == "en_US-libritts_r-medium"
    assert c.ws.port == 8765
    assert c.http.port == 8080
    assert c.streaming_tts.idle_pause_ms == 1000
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
    assert c.stt == cfg_mod.WyomingEndpoint("whisper-host", 9999)
    assert c.tts.host == "piper-host"
    assert c.tts.port == 1234
    assert c.tts.voice == "en_US-amy-medium"
    assert c.ws.port == 9999
    assert c.http.port == 9090
    assert c.streaming_tts.idle_pause_ms == 777
    assert c.data_dir == "/var/lib/vauxr"
    assert c.log_level == "debug"


def test_load_config_missing_device_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVICE_TOKEN", raising=False)
    assert cfg_mod.load_config().device.token == ""


def test_parse_wyoming_url_without_tcp_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Node parser strips a leading `tcp://` but otherwise accepts plain
    # `host:port`. Match that behavior.
    monkeypatch.setenv("WHISPER_URL", "plain:5555")
    c = cfg_mod.load_config()
    assert c.stt == cfg_mod.WyomingEndpoint("plain", 5555)


def test_get_config_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    a = cfg_mod.get_config()
    monkeypatch.setenv("DEVICE_TOKEN", "different")
    b = cfg_mod.get_config()
    assert a is b  # cached
    cfg_mod.reset_config()
    c = cfg_mod.get_config()
    assert c is not a


@pytest.mark.parametrize("neutral", [True, False])
def test_neutral_speech_env_precedence_and_empty_fallback(monkeypatch, neutral):
    for name, legacy, old, new in (
        ("STT_URL", "WHISPER_URL", "old-stt:11001", "new-stt:11002"),
        ("TTS_URL", "PIPER_URL", "old-tts:12001", "new-tts:12002"),
        ("TTS_VOICE", "PIPER_VOICE", "old-voice", "new-voice"),
    ):
        monkeypatch.setenv(legacy, old)
        monkeypatch.setenv(name, new if neutral else "")
    cfg = cfg_mod.load_config()
    assert cfg.stt == cfg_mod.WyomingEndpoint("new-stt" if neutral else "old-stt", 11002 if neutral else 11001)
    assert cfg.tts.host == ("new-tts" if neutral else "old-tts")
    assert cfg.tts.port == (12002 if neutral else 12001)
    assert cfg.tts.voice == ("new-voice" if neutral else "old-voice")


@pytest.mark.parametrize("value", [None, ""])
def test_empty_speech_environment_retains_defaults(monkeypatch, value):
    for name in ("STT_URL", "TTS_URL", "TTS_VOICE", "WHISPER_URL", "PIPER_URL", "PIPER_VOICE"):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    cfg = cfg_mod.load_config()
    assert cfg.stt == cfg_mod.WyomingEndpoint("whisper", 10300)
    assert cfg.tts == cfg_mod.WyomingTTSConfig("piper", 10200, "en_US-libritts_r-medium")


def test_neutral_speech_environment_without_legacy_values(monkeypatch):
    for name in ("WHISPER_URL", "PIPER_URL", "PIPER_VOICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STT_URL", "tcp://recognizer-east:10400")
    monkeypatch.setenv("TTS_URL", "tcp://speaker-east:10401")
    monkeypatch.setenv("TTS_VOICE", "narrator-b")
    cfg = cfg_mod.load_config()
    assert cfg.stt == cfg_mod.WyomingEndpoint("recognizer-east", 10400)
    assert cfg.tts == cfg_mod.WyomingTTSConfig("speaker-east", 10401, "narrator-b")
