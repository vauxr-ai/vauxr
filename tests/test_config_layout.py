"""Authoritative stores, diagnostics and variable-length identity contracts (#94)."""

import hashlib
import json
import logging

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import vauxr.agents.registry as agents
import vauxr.speech.store as speech
from tests.test_enrollment import approve, owner_resolver, request, setup, signed
from vauxr import config
from vauxr.config_files import ConfigError
from vauxr.provisioning.enrollment import EnrollmentError
from vauxr.provisioning.pairing_audio import DEFAULT_PROMPTS, load_prompts, save_prompts
from vauxr.speech.catalog import shipped_backends

assert setup


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_URL", "")
    monkeypatch.delenv("DEVICE_ID_LENGTH", raising=False)
    config.reset_config()
    agents._reset_for_tests()
    speech._store = None
    yield
    config.reset_config()
    agents._reset_for_tests()
    speech._store = None


@pytest.mark.parametrize("length", [1, 8, 16, 32, 64])
def test_new_id_length_is_selected_by_server(setup, monkeypatch, length):
    monkeypatch.setenv("DEVICE_ID_LENGTH", str(length))
    service, _, _ = setup
    key, row = request(service)
    digest = hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()
    assert row["device_id"] == "dev_" + digest[:length]
    assert service.execute("prove", signed(key, row, "prove"))["status"] == "ready"


@pytest.mark.parametrize("length", ["0", "65", "-1", "abc", "3.5"])
def test_bad_id_length_logs_actionable_error(monkeypatch, caplog, length):
    monkeypatch.setenv("DEVICE_ID_LENGTH", length)
    with pytest.raises(ConfigError, match="DEVICE_ID_LENGTH.*1 to 64"):
        config.get_config()
    assert "DEVICE_ID_LENGTH" in caplog.text


@pytest.mark.parametrize("old_id", ["dev_a", "dev_" + "a" * 64, "old-device-id", "dev_" + "b" * 90])
def test_existing_key_binding_keeps_any_old_id(setup, monkeypatch, old_id):
    service, _, _ = setup
    key = Ed25519PrivateKey.generate()
    with service.store.transaction():
        from vauxr.provisioning.lifecycle_schema import empty_state

        state = empty_state()
        state["bindings"][old_id] = {
            "public_key": key.public_key().public_bytes_raw().hex(),
            "kind": "physical",
        }
        service.store.lifecycle = state
        service.store.replace(service.store.records)
    monkeypatch.setenv("DEVICE_ID_LENGTH", "8")
    _, row = request(service, key=key)
    assert row["device_id"] == old_id
    assert service.execute("prove", signed(key, row, "prove"))["status"] == "ready"


def test_length_change_keeps_enrolled_identity_and_recovery(setup, monkeypatch):
    service, owner, cookie = setup
    key, row = request(service)
    proof = service.execute("prove", signed(key, row, "prove"))
    resolve = owner_resolver(owner, cookie)
    approve(service, row, proof["code"], resolve)
    issued = service.execute("redeem", signed(key, row, "redeem"))
    monkeypatch.setenv("DEVICE_ID_LENGTH", "32")
    config.reset_config()
    _, next_row = request(service, key=key)
    assert next_row["device_id"] == issued["device_id"]
    assert len(next_row["device_id"]) == 20


def test_prefix_collision_cannot_claim_another_key(setup, monkeypatch):
    monkeypatch.setenv("DEVICE_ID_LENGTH", "1")
    service, owner, cookie = setup
    key, row = request(service)
    proof = service.execute("prove", signed(key, row, "prove"))
    approve(service, row, proof["code"], owner_resolver(owner, cookie))
    service.execute("redeem", signed(key, row, "redeem"))
    other = Ed25519PrivateKey.generate()
    while hashlib.sha256(other.public_key().public_bytes_raw()).hexdigest()[0] != row["device_id"][-1]:
        other = Ed25519PrivateKey.generate()
    before = service.store.path.read_bytes()
    with pytest.raises(EnrollmentError, match="already_owned"):
        request(service, key=other)
    assert service.store.path.read_bytes() == before


@pytest.mark.parametrize("scheme", ["ws", "wss", "http", "https"])
def test_direct_websocket_endpoint_schemes(tmp_path, scheme):
    url = f"{scheme}://agent.example:18789/"
    (tmp_path / "config.json").write_text(json.dumps({"openclaw": {"url": url}}))
    assert config.get_config().openclaw.url == url


def test_pairing_edit_preserves_other_server_settings(tmp_path):
    path = tmp_path / "config.json"
    settings = {
        "server": {"http_port": 9090},
        "realtime": {"enabled": True},
        "openclaw": {"url": "wss://agent.example"},
    }
    path.write_text(json.dumps(settings))
    save_prompts({"intro": "Pair now", "code": "Code: {code}"})
    saved = json.loads(path.read_text())
    assert {key: saved[key] for key in settings} == settings
    config.reset_config()
    assert config.get_config().http.port == 9090
    assert load_prompts() == {"intro": "Pair now", "code": "Code: {code}"}


@pytest.mark.parametrize(
    "filename,payload,loader",
    [
        (
            "config.json",
            {"pairing": {"prompts": {"intro": "x", "code": "missing placeholder"}}},
            config.get_config,
        ),
        ("agents.json", {"version": 1, "agents": [], "active_agent": "missing"}, agents.load),
        ("speech.json", {"version": 1, "providers": [], "defaults": {}, "devices": {}}, speech.get_store),
        ("config.json", {"server": {"http_port": True}}, config.get_config),
        ("config.json", {"openclaw": {"url": "ftp://agent.example"}}, config.get_config),
        ("config.json", {"openclaw": {"url": "wss://user:secret@agent.example"}}, config.get_config),
    ],
)
def test_invalid_file_reports_path_and_required_fix(tmp_path, caplog, filename, payload, loader):
    (tmp_path / filename).write_text(json.dumps(payload))
    with pytest.raises(ConfigError, match="Correct this setting and restart") as error:
        loader()
    assert str(tmp_path / filename) in str(error.value)
    assert str(tmp_path / filename) in caplog.text


@pytest.mark.parametrize(
    "filename,loader",
    [("config.json", config.get_config), ("agents.json", agents.load), ("speech.json", speech.get_store)],
)
def test_invalid_json_never_logs_contents(tmp_path, caplog, filename, loader):
    (tmp_path / filename).write_text('{"private": "synthetic-private-value",')
    caplog.set_level(logging.ERROR)
    with pytest.raises(ConfigError, match="valid JSON"):
        loader()
    assert "synthetic-private-value" not in caplog.text
    assert "valid JSON" in caplog.text


def test_shipped_providers_are_persisted_without_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = speech.get_store()
    data = json.loads(store.path.read_text())
    assert {row["id"] for row in data["providers"]} == {
        "whisper",
        "piper",
        "parakeet-v3",
        "kokoro",
        "openai-tts",
        "openai-live",
    }
    assert {row["kind"] for row in data["providers"]} == {"stt", "tts", "realtime"}
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-key")
    config.reset_config()
    restarted = speech.get_store()
    assert restarted.path.read_bytes() == store.path.read_bytes()
    assert "synthetic-private-key" not in json.dumps(restarted.view())


def test_realtime_uses_registry_id_model_and_voice(tmp_path):
    backends = shipped_backends(config.get_config())
    store = speech.SpeechStore(tmp_path, backends)
    data = json.loads(store.path.read_text())
    live = next(row for row in data["providers"] if row["kind"] == "realtime")
    live.update(id="custom-live", model="configured-live-model", voices=["custom-voice"])
    data["defaults"].update(realtime_backend="custom-live", realtime_voice="custom-voice")
    store.path.write_text(json.dumps(data))
    restarted = speech.SpeechStore(tmp_path, backends)
    restarted.update({"mode": "realtime", "realtime_backend": "custom-live"})
    assert restarted.voice_settings() == {
        "mode": "realtime",
        "realtime_backend": "custom-live",
        "realtime_model": "configured-live-model",
        "realtime_voice": "custom-voice",
    }
    with pytest.raises(ConfigError, match="realtime_voice"):
        restarted.update({"realtime_voice": "marin"})


def test_direct_selection_never_overwrites_config(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_URL", "wss://agent.example")
    config.initialize_config()
    path = tmp_path / "config.json"
    before = path.read_bytes()
    agents.load()
    assert agents.activate("openclaw-direct")
    assert path.read_bytes() == before
    assert json.loads((tmp_path / "agents.json").read_text())["active_agent"] == "openclaw-direct"
    assert "openclawDirectActive" not in path.read_text()


def test_legacy_speech_and_prompts_are_not_read(tmp_path):
    (tmp_path / "speech-settings.json").write_text("malformed legacy content")
    (tmp_path / "speech-providers.json").write_text("malformed legacy content")
    (tmp_path / "pairing-prompts.json").write_text("malformed legacy content")
    assert speech.get_store().resolve().tts.id == "piper"
    assert load_prompts() == DEFAULT_PROMPTS


def test_cleanup_removes_only_obsolete_configuration(tmp_path, caplog):
    from vauxr.config_files import remove_obsolete_files

    legacy = ("channels.json", "speech-providers.json", "speech-settings.json", "pairing-prompts.json")
    current = ("agents.json", "authz.json", "speech.json", "config.json", "devices.json")
    for name in (*legacy, *current):
        (tmp_path / name).write_text("synthetic contents")
    caplog.set_level(logging.INFO)
    remove_obsolete_files(tmp_path)
    assert all(not (tmp_path / name).exists() for name in legacy)
    assert all((tmp_path / name).read_text() == "synthetic contents" for name in current)
    assert "Removed obsolete configuration" in caplog.text
    assert "synthetic contents" not in caplog.text


@pytest.mark.parametrize(
    "entry",
    [{"barge_in": "yes"}, {"pipeline_mode": "bad"}, {"name": "bad\u0000name"}, {"button_actions": []}],
)
def test_startup_device_validation_has_actionable_errors(tmp_path, entry, caplog):
    from vauxr.devices.config import load_device_configs

    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"old-device-id": entry}))
    with pytest.raises(ConfigError, match="devices.*"):
        load_device_configs(str(tmp_path), strict=True)
    assert str(path) in caplog.text
    assert "Correct this setting" in caplog.text


def test_pipeline_mode_has_one_persisted_home_and_inherits_global_default(tmp_path):
    from vauxr.devices import registry as devices

    devices.reset()
    store = speech.get_store()
    store.update({"mode": "realtime"})
    assert devices.get_config_for("new")["pipeline_mode"] == "realtime"
    devices.update_config("fixed", {"pipeline_mode": "standard", "name": "Kitchen"})
    assert devices.get_config_for("fixed").get("pipeline_mode", "standard") == "standard"
    assert json.loads((tmp_path / "devices.json").read_text())["fixed"] == {"name": "Kitchen"}
    assert json.loads(store.path.read_text())["devices"]["fixed"]["mode"] == "standard"
    restarted = speech.SpeechStore(tmp_path, ())
    assert restarted.voice_settings("new")["mode"] == "realtime"
    assert restarted.voice_settings("fixed")["mode"] == "standard"
    devices.reset()


@pytest.mark.parametrize("after_rename", [False, True])
def test_selection_write_failure_matches_visible_disk(tmp_path, monkeypatch, after_rename):
    import vauxr.config_files as files
    from vauxr.agents.registry import Agent

    agents.load()
    a = Agent("a", "A", "openclaw", False, "2026-10-03T00:00:00Z")
    b = Agent("b", "B", "openclaw", False, "2026-10-03T00:00:00Z")
    agents.register(a)
    agents.register(b)
    assert agents.activate("a")
    original = files.write_json

    def failed_write(path, data):
        if after_rename:
            original(path, data)
        raise OSError("synthetic durability failure")

    monkeypatch.setattr(files, "write_json", failed_write)
    with pytest.raises(OSError, match="synthetic durability"):
        agents.activate("b")
    disk = json.loads((tmp_path / "agents.json").read_text())
    assert disk["active_agent"] == ("b" if after_rename else "a")
    assert agents.get_active().id == disk["active_agent"]


def test_missing_integration_registry_is_reported_without_reconstruction(tmp_path, monkeypatch):
    from tests.test_integration import ack_body, deliver
    from vauxr.auth import service as auth
    from vauxr.auth.owner import OwnerAuth
    from vauxr.auth.store import CredentialStore
    from vauxr.provisioning.integration import Integration

    store = CredentialStore(tmp_path / "authz.json")
    owner = OwnerAuth(store)
    owner.initialize()
    result = owner.claim(owner.console_claim())
    owner.acknowledge(result["save_acknowledgement"], True)
    cookie, _ = owner.login(result["operator_token"])
    service = Integration(store, "https://owner.example")
    monkeypatch.setattr(auth, "get_store", lambda: store)
    body, _, issued = deliver((service, owner_resolver(owner, cookie)))
    service.execute("ack", ack_body(body, issued))
    data = json.loads((tmp_path / "agents.json").read_text())
    assert any(row["id"] == issued["agent_id"] for row in data["agents"])
    (tmp_path / "agents.json").write_text(json.dumps({"version": 1, "agents": [], "active_agent": ""}))
    agents.load()
    with pytest.raises(ConfigError, match="missing enrolled integration metadata"):
        agents.validate_authority()
    assert json.loads((tmp_path / "agents.json").read_text())["agents"] == []


def test_ack_metadata_failure_keeps_credentials_pending_and_retry_is_safe(tmp_path, monkeypatch):
    import vauxr.config_files as files
    from tests.test_integration import ack_body, deliver
    from vauxr.auth import service as auth
    from vauxr.auth.owner import OwnerAuth
    from vauxr.auth.store import CredentialStore
    from vauxr.provisioning.integration import Integration

    store = CredentialStore(tmp_path / "authz.json")
    owner = OwnerAuth(store)
    owner.initialize()
    result = owner.claim(owner.console_claim())
    owner.acknowledge(result["save_acknowledgement"], True)
    cookie, _ = owner.login(result["operator_token"])
    service = Integration(store, "https://owner.example")
    monkeypatch.setattr(auth, "get_store", lambda: store)
    body, _, issued = deliver((service, owner_resolver(owner, cookie)))
    agents.load()
    original = files.write_json
    events = []

    def fail_metadata(path, data):
        if any(row.get("id") == issued["agent_id"] for row in data.get("agents", [])):
            assert store.authenticate(issued["credential"]) is None
            events.append("metadata-before-authority")
            raise OSError("synthetic metadata failure")
        original(path, data)

    monkeypatch.setattr(files, "write_json", fail_metadata)
    with pytest.raises(OSError, match="synthetic metadata failure"):
        service.execute("ack", ack_body(body, issued))
    assert events == ["metadata-before-authority"]
    assert CredentialStore(store.path).authenticate(issued["credential"]) is None
    assert store.integration["requests"][body["request_id"]]["state"] == "delivered"
    assert agents.get_by_id(issued["agent_id"]) is None
    monkeypatch.setattr(files, "write_json", original)
    assert service.execute("ack", ack_body(body, issued))["state"] == "completed"
    assert store.authenticate(issued["credential"])
    assert agents.get_by_id(issued["agent_id"])
    assert service.execute("ack", ack_body(body, issued))["state"] == "completed"
    assert (
        len(
            [
                row
                for row in json.loads((tmp_path / "agents.json").read_text())["agents"]
                if row["id"] == issued["agent_id"]
            ]
        )
        == 1
    )
