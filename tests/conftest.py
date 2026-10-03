"""Each test gets a writable, isolated persistent configuration directory."""

import pytest

from vauxr import config
from vauxr.agents import registry
from vauxr.auth import service as auth
from vauxr.speech import store as speech


@pytest.fixture(autouse=True)
def isolated_data_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config.reset_config()
    registry._reset_for_tests()
    auth._store = None
    speech._store = None
    yield
    config.reset_config()
    registry._reset_for_tests()
    auth._store = None
    speech._store = None
