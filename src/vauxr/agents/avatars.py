"""Bounded raster avatars, independent of routing and credential configuration."""

from __future__ import annotations

import hashlib
import io
import os
import warnings
from pathlib import Path
from urllib.parse import quote

from vauxr.config import get_config

MAX_BYTES = 2 * 1024 * 1024


def path(agent_id: str) -> Path:
    # Registry IDs are opaque, not filesystem names.
    return (
        Path(get_config().data_dir)
        / "agent-avatars"
        / (hashlib.sha256(agent_id.encode()).hexdigest() + ".png")
    )


def metadata(agent_id: str) -> dict:
    target = path(agent_id)
    if not target.exists():
        return {}
    revision = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"avatar_url": f"/api/agents/{quote(agent_id, safe='')}/avatar", "avatar_revision": revision}


def normalize(raw: bytes, content_type: str) -> bytes:
    from PIL import Image

    if content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("Use a PNG, JPEG or WebP image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if (
                    image.format
                    != {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[content_type]
                ):
                    raise ValueError()
                if image.width > 2048 or image.height > 2048 or image.width * image.height > 4194304:
                    raise ValueError()
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                output = io.BytesIO()
                image.convert("RGBA").save(output, format="PNG")
                return output.getvalue()
    except Exception as error:
        raise ValueError("Invalid image; maximum dimensions are 2048 by 2048") from error


def save(agent_id: str, data: bytes) -> None:
    import tempfile

    target = path(agent_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
        _sync_directory(target.parent)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def remove(agent_id: str) -> None:
    target = path(agent_id)
    if target.exists():
        target.unlink()
        _sync_directory(target.parent)
