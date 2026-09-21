from pathlib import Path

import pytest

from vauxr.config import get_config
from vauxr.speech.store import SpeechStore
from vauxr.speech.catalog import load_backends


def test_modes_preserve_independent_selections_and_restart(tmp_path: Path) -> None:
    store = SpeechStore(tmp_path, load_backends(get_config()))
    assert store.voice_settings()["mode"] == "standard"
    standard = store.resolve()
    store.update({"mode": "realtime", "realtime_voice": "cedar"}, "browser")
    store.update({"mode": "standard"}, "browser")
    assert store.resolve("browser") == standard
    store = SpeechStore(tmp_path, load_backends(get_config()))
    assert store.voice_settings("browser") == {"mode": "standard", "realtime_model": "gpt-live-1", "realtime_voice": "cedar"}
    store.update({"mode": "realtime"}, "browser")
    assert store.voice_settings("browser")["realtime_voice"] == "cedar"
    assert store.voice_settings("other")["mode"] == "standard"
    for patch in ({"mode": "invalid"}, {"realtime_voice": "invalid"}, {"realtime_model": "other-model"}):
        with pytest.raises(ValueError):
            store.update(patch)
