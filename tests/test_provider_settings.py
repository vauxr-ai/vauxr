"""Persistent settings contracts, credential isolation and owner boundaries."""

import json
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tests.auth_helpers import owner_headers
from vauxr.config import reset_config
from vauxr.settings.http import discover_profile
from vauxr.settings.providers import ProviderStore, SecretStore, resolve_profile
from vauxr.web.server import make_http_app


def profile(**patch):
    return {
        "name": "Shared OpenAI",
        "provider": "openai",
        "endpoint": "https://api.openai.com",
        "model": "test-model",
        "auth": "inline",
        **patch,
    }


def test_profile_secret_lifecycle_restart_and_env(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = ProviderStore(tmp_path)
    result = store.save("shared", profile(credential="private-test-key"))
    assert result["profiles"][0]["credential_configured"] is True
    assert "private-test-key" not in json.dumps(result)
    assert "private-test-key" not in store.path.read_text()
    assert os.stat(tmp_path / "settings-secrets.json").st_mode & 0o777 == 0o600
    assert resolve_profile("shared", tmp_path) == {
        "provider": "openai",
        "endpoint": "https://api.openai.com",
        "model": "test-model",
        "api_key": "private-test-key",
    }
    store.save("shared", profile(name="Renamed"))
    assert resolve_profile("shared", tmp_path)["api_key"] == "private-test-key"
    store.save("shared", profile(clear_credential=True))
    with pytest.raises(ValueError, match="not configured"):
        resolve_profile("shared", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-test-key")
    store.save("shared", profile(auth="environment"))
    assert resolve_profile("shared", tmp_path)["api_key"] == "environment-test-key"
    assert "environment-test-key" not in json.dumps(store.view())
    assert "private-test-key" not in caplog.text


@pytest.mark.parametrize(
    "patch",
    [
        {"credential": "", "clear_credential": True},
        {"credential": "x", "clear_credential": True},
        {"clear_credential": "yes"},
        {"endpoint": "https://attacker.example"},
        {"extra": 1},
        {"auth": "unknown"},
        {"model": ""},
        {"credential": "x\nheader"},
    ],
)
def test_profile_rejects_invalid_without_mutation(tmp_path, patch):
    store = ProviderStore(tmp_path)
    store.save("shared", profile(credential="retained"))
    previous = store.path.read_bytes(), store.secrets.path.read_bytes()
    with pytest.raises(ValueError):
        store.save("shared", profile(**patch))
    assert previous == (store.path.read_bytes(), store.secrets.path.read_bytes())


@pytest.mark.parametrize(
    "endpoint", ["http://ollama.local:11434", "http://192.168.1.9:11434", "https://[::1]:11434/base"]
)
def test_local_and_lan_ollama(tmp_path, endpoint):
    store = ProviderStore(tmp_path)
    store.save("local", profile(provider="ollama", endpoint=endpoint, auth="none"))
    assert resolve_profile("local", tmp_path)["endpoint"] == endpoint
    assert resolve_profile("local", tmp_path)["api_key"] is None


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///tmp/key",
        "http://user:secret@host",
        "http://host?q=secret",
        "http://host/#key",
        "http://host:999999",
    ],
)
def test_reject_credential_bearing_or_invalid_endpoints(tmp_path, endpoint):
    with pytest.raises(ValueError):
        ProviderStore(tmp_path).save("local", profile(provider="ollama", endpoint=endpoint, auth="none"))


@pytest.fixture
async def settings_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_HTTPS_ORIGIN", "https://owner.example")
    monkeypatch.setenv("OWNER_TRUSTED_PROXIES", "127.0.0.1/32")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reset_config()
    async with TestClient(TestServer(make_http_app())) as client:
        yield client
    reset_config()


async def test_owner_csrf_masking_and_real_tts_resolution(settings_client, caplog):
    client = settings_client
    for path in ("/api/settings/providers", "/api/settings/speech", "/api/settings/plugins"):
        assert (await client.get(path)).status == 401
    headers = owner_headers(client)
    path = "/api/settings/providers/profiles/shared"
    assert (
        await client.put(
            path,
            json=profile(credential="hidden"),
            headers={k: v for k, v in headers.items() if k != "X-CSRF-Token"},
        )
    ).status == 403
    response = await client.put(path, json=profile(credential="hidden"), headers=headers)
    assert response.status == 200
    assert "hidden" not in await response.text()
    response = await client.put(
        "/api/settings/providers/speech-auth",
        json={"backend_id": "openai-tts", "profile_id": "shared"},
        headers=headers,
    )
    assert response.status == 200
    from vauxr.speech.openai import _headers
    from vauxr.speech.store import get_store

    assert _headers(get_store().backends["openai-tts"])["Authorization"] == "Bearer hidden"
    assert (
        await client.put(
            "/api/settings/providers/speech-auth",
            json={"backend_id": "openai-live", "profile_id": "shared"},
            headers=headers,
        )
    ).status == 400
    assert "hidden" not in caplog.text


async def test_speech_registry_edit_validates_references_and_preserves_policies(settings_client):
    client = settings_client
    headers = owner_headers(client)
    before = await (await client.get("/api/settings/speech", headers=headers)).json()
    piper = next(b for b in before["providers"] if b["id"] == "piper")
    from vauxr.speech.store import get_store

    original = json.loads(get_store().path.read_text())
    bad = {**piper, "voices": ["unreferenced"]}
    assert (await client.put("/api/settings/speech/piper", json=bad, headers=headers)).status == 400
    assert json.loads(get_store().path.read_text()) == original
    good = {**piper, "host": "my-piper.local"}
    assert (await client.put("/api/settings/speech/piper", json=good, headers=headers)).status == 200
    saved = json.loads(get_store().path.read_text())
    assert {k: v for k, v in saved.items() if k != "providers"} == {
        k: v for k, v in original.items() if k != "providers"
    }
    assert (
        await client.put("/api/settings/speech/piper", json={**good, "kind": "stt"}, headers=headers)
    ).status == 400


async def test_model_discovery_metadata_only_no_redirects(settings_client):
    calls = []

    async def tags(request):
        calls.append(request.path)
        return web.json_response({"models": [{"name": "local-model"}, {"name": "local-model"}]})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    async with TestServer(app) as upstream:
        ProviderStore().save(
            "local", profile(provider="ollama", endpoint=str(upstream.make_url("")).rstrip("/"), auth="none")
        )
        result = await discover_profile("local")
        assert result == {"models": ["local-model"], "source": "ollama-tags"}
        assert calls == ["/api/tags"]


def test_secret_store_atomic_replacement_failure_preserves_old(tmp_path, monkeypatch):
    store = SecretStore(tmp_path)
    store.change({"provider:test": "old"}, [])

    def fail(*_):
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.change({"provider:test": "new"}, [])
    assert store.read() == {"provider:test": "old"}
    assert not list(tmp_path.glob(".settings-secrets-*"))


async def test_plugin_http_declared_only_owner_and_csrf(settings_client, monkeypatch):
    import vauxr.plugins.settings as plugins

    monkeypatch.setattr(plugins, "_registered", {})
    plugins.register(
        plugins.PluginSettings(
            "real-test-registration",
            "Registered test plugin",
            (
                plugins.SettingField("enabled", "Enabled", "boolean", default=False),
                plugins.SettingField("token", "Token", "secret"),
            ),
        )
    )
    client = settings_client
    headers = owner_headers(client)
    path = "/api/settings/plugins/real-test-registration"
    assert (await client.put(path, json={"secrets": {"token": "test-plugin-key"}})).status == 401
    no_csrf = {k: v for k, v in headers.items() if k != "X-CSRF-Token"}
    assert (await client.put(path, json={"values": {"enabled": True}}, headers=no_csrf)).status == 403
    result = await client.put(
        path, json={"values": {"enabled": True}, "secrets": {"token": "test-plugin-key"}}, headers=headers
    )
    assert result.status == 200
    assert "test-plugin-key" not in await result.text()
    assert (await client.put(path, json={"values": {"invented": True}}, headers=headers)).status == 400
    assert (await client.put("/api/settings/plugins/unregistered", json={}, headers=headers)).status == 400
    assert (await client.put(path, json={"clear_secrets": ["token"]}, headers=headers)).status == 200
    data = await (await client.get("/api/settings/plugins", headers=headers)).json()
    assert data["plugins"][0]["secrets"] == {"token": False}


async def test_add_deployment_and_active_edit_conflict(settings_client, monkeypatch):
    from types import SimpleNamespace

    from vauxr.devices import registry
    from vauxr.speech.store import get_store

    client = settings_client
    headers = owner_headers(client)
    new = {
        "id": "household-stt",
        "kind": "stt",
        "adapter": "wyoming",
        "model": "household",
        "host": "speech.local",
        "port": 10300,
        "voices": [],
    }
    assert (await client.put("/api/settings/speech/household-stt", json=new, headers=headers)).status == 200
    assert get_store().backends["household-stt"].host == "speech.local"
    device = SimpleNamespace(id="test-active", state="listening")
    monkeypatch.setattr(registry, "get_all", lambda: [device])
    monkeypatch.setattr(registry, "get", lambda _: device)
    original = get_store().path.read_bytes()
    from dataclasses import asdict

    selected = asdict(get_store().resolve().stt)
    selected["host"] = "new-host.local"
    response = await client.put("/api/settings/speech/" + selected["id"], json=selected, headers=headers)
    assert response.status == 409
    assert get_store().path.read_bytes() == original


async def test_wyoming_describe_returns_suggestions_without_saving(settings_client):
    import asyncio
    from dataclasses import asdict

    from vauxr.settings.http import discover_speech
    from vauxr.speech.store import get_store
    from vauxr.speech.wyoming_protocol import WyomingEvent, encode_event

    async def peer(reader, writer):
        await reader.readline()
        writer.write(
            encode_event(WyomingEvent("info", {"tts": [{"voices": [{"name": "discovered-voice"}]}]}))
        )
        await writer.drain()
        writer.close()

    async with await asyncio.start_server(peer, "127.0.0.1", 0) as server:
        backend = asdict(get_store().backends["piper"])
        backend.update(host="127.0.0.1", port=server.sockets[0].getsockname()[1], voices=list(backend["voices"]))
        from vauxr.settings.http import save_backend

        save_backend("piper", backend)
        original = get_store().path.read_bytes()
        result = await discover_speech("piper")
        assert result["voices"] == ["discovered-voice"]
        assert get_store().path.read_bytes() == original
