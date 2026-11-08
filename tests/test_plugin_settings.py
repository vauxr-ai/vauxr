import json

import pytest

from vauxr.plugins.settings import PluginSettings, PluginStore, SettingField, _registered, register


@pytest.fixture(autouse=True)
def registrations():
    previous = dict(_registered)
    _registered.clear()
    yield
    _registered.clear()
    _registered.update(previous)


def schema():
    return PluginSettings(
        "test-plugin",
        "Test plugin",
        (
            SettingField("enabled", "Enabled", "boolean", default=False),
            SettingField("attempts", "Attempts", "integer", default=2, min=1, max=5),
            SettingField("mode", "Mode", "string", choices=("quiet", "normal"), default="quiet"),
            SettingField("token", "Credential", "secret"),
        ),
    )


def test_empty_catalog_and_strict_registration(tmp_path):
    assert PluginStore(tmp_path).view() == {"plugins": []}
    register(schema())
    with pytest.raises(ValueError):
        register(schema())
    with pytest.raises(ValueError):
        register(
            PluginSettings("bad", "Bad", (SettingField("secret", "Secret", "secret", default="leaked"),))
        )


def test_persist_restart_mask_update_clear(tmp_path):
    register(schema())
    store = PluginStore(tmp_path)
    result = store.save(
        "test-plugin", {"values": {"enabled": True}, "secrets": {"token": "private-plugin-token"}}
    )
    assert result["plugins"][0]["secrets"] == {"token": True}
    assert "private-plugin-token" not in json.dumps(result)
    assert "private-plugin-token" not in store.path.read_text()
    assert PluginStore(tmp_path).values("test-plugin")["token"] == "private-plugin-token"
    store.save("test-plugin", {"values": {"attempts": 3}})
    assert store.values("test-plugin")["token"] == "private-plugin-token"
    store.save("test-plugin", {"clear_secrets": ["token"]})
    assert "token" not in store.values("test-plugin")


@pytest.mark.parametrize(
    "body",
    [
        {"values": {"unknown": "x"}},
        {"values": {"attempts": True}},
        {"values": {"enabled": 1}},
        {"values": {"attempts": 99}},
        {"values": {"mode": "invented"}},
        {"values": {"token": "leak"}},
        {"secrets": {"enabled": "leak"}},
        {"secrets": {"token": ""}},
        {"clear_secrets": ["enabled"]},
        {"secrets": {"token": "x"}, "clear_secrets": ["token"]},
        {"values": {"token": "x"}, "secrets": {"token": "x"}},
        {"extra": True},
    ],
)
def test_invalid_updates_do_not_mutate(tmp_path, body):
    register(schema())
    store = PluginStore(tmp_path)
    store.save("test-plugin", {"values": {"enabled": True}})
    previous = store.path.read_bytes(), store.secrets.path.read_bytes()
    with pytest.raises(ValueError):
        store.save("test-plugin", body)
    assert previous == (store.path.read_bytes(), store.secrets.path.read_bytes())


def test_required_secret_and_unknown_plugin(tmp_path):
    register(PluginSettings("required", "Required", (SettingField("key", "Key", "secret", required=True),)))
    store = PluginStore(tmp_path)
    with pytest.raises(ValueError):
        store.save("required", {"values": {}})
    store.save("required", {"secrets": {"key": "configured"}})
    with pytest.raises(ValueError):
        store.save("required", {"clear_secrets": ["key"]})
    with pytest.raises(ValueError):
        store.save("not-registered", {})
