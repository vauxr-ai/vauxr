"""Validated JSON configuration boundaries and durable, serialized writes."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("vauxr.config")


class ConfigError(ValueError):
    """Actionable diagnostic that contains paths and schema labels, never values."""


def invalid(path: Path, field: str, requirement: str) -> ConfigError:
    message = f"{path}: {field}: {requirement}. Correct this setting and restart Vauxr"
    log.error(message)
    return ConfigError(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate keys")
        result[key] = value
    return result


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError):
        raise invalid(path, "$", "expected valid JSON with unique keys") from None
    except OSError:
        raise invalid(path, "$", "configuration must be readable") from None


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def update_json(path: Path, change: Callable[[dict], dict]) -> dict:
    with locked(path):
        data = read_json(path) if path.exists() else {}
        if not isinstance(data, dict):
            raise invalid(path, "$", "expected an object")
        result = change(data)
        if result != data or not path.exists():
            write_json(path, result)
        return result


def remove_obsolete_files(directory: Path) -> None:
    """Legacy configuration is discarded; no migration or preservation is attempted."""
    for name in ("channels.json", "speech-providers.json", "speech-settings.json", "pairing-prompts.json"):
        path = directory / name
        if path.exists():
            try:
                path.unlink()
            except OSError:
                raise invalid(path, "$", "obsolete configuration must be removable") from None
            log.info("Removed obsolete configuration file %s", path)
