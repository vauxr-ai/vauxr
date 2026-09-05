"""Global named webhooks, persisted in DATA_DIR/webhooks.json.

Configured in Settings, then selected per device gesture. The authorization
header is stored with the webhook so Home Assistant (or any bearer-token
receiver) can be targeted without putting secrets in devices.json.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from config import get_config

log = logging.getLogger("vauxr.webhooks")


@dataclass
class Webhook:
    id: str
    name: str
    url: str
    authorization: str = ""
    body: dict[str, Any] | None = None


_UNSET: Any = object()


_webhooks: list[Webhook] = []
_loaded = False


def _path() -> str:
    return os.path.join(get_config().data_dir, "webhooks.json")


def _ensure_data_dir() -> None:
    os.makedirs(get_config().data_dir, exist_ok=True)


def _save() -> None:
    _ensure_data_dir()
    payload = {
        "webhooks": [
            {
                "id": w.id,
                "name": w.name,
                "url": w.url,
                **({"authorization": w.authorization} if w.authorization else {}),
                **({"body": w.body} if w.body else {}),
            }
            for w in _webhooks
        ]
    }
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load() -> None:
    """(Re)load webhooks.json from disk."""
    global _webhooks, _loaded
    _ensure_data_dir()
    p = _path()
    loaded: list[Webhook] = []
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as err:
            log.warning("Failed to parse %s: %s", p, err)
            raw = None
        entries: list[Any]
        if isinstance(raw, dict) and isinstance(raw.get("webhooks"), list):
            entries = raw["webhooks"]
        elif isinstance(raw, list):
            entries = raw
        else:
            entries = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            wid = item.get("id")
            name = item.get("name")
            url = item.get("url")
            if not isinstance(wid, str) or not isinstance(name, str) or not isinstance(url, str):
                continue
            auth = item.get("authorization")
            raw_body = item.get("body")
            body = raw_body if isinstance(raw_body, dict) else None
            loaded.append(
                Webhook(
                    id=wid,
                    name=name,
                    url=url,
                    authorization=auth if isinstance(auth, str) else "",
                    body=body,
                )
            )
    _webhooks = loaded
    _loaded = True


def _ensure_loaded() -> None:
    if not _loaded:
        load()


def reset_for_tests() -> None:
    global _webhooks, _loaded
    _webhooks = []
    _loaded = False


def get_all() -> list[Webhook]:
    _ensure_loaded()
    return list(_webhooks)


def get(webhook_id: str) -> Webhook | None:
    _ensure_loaded()
    for w in _webhooks:
        if w.id == webhook_id:
            return w
    return None


def validate_url(url: str) -> str | None:
    """Return an error message, or None if the URL is acceptable."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "url must be an http:// or https:// URL"
    return None


def parse_body(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Normalize a webhook JSON body. ``None`` / empty string clears it."""
    if value is None:
        return None, None
    if isinstance(value, str):
        if not value.strip():
            return None, None
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None, "body must be valid JSON"
    if not isinstance(value, dict):
        return None, "body must be a JSON object"
    return value, None


def create(
    name: str,
    url: str,
    authorization: str = "",
    body: dict[str, Any] | None = None,
) -> Webhook:
    _ensure_loaded()
    hook = Webhook(
        id=f"wh_{uuid.uuid4().hex[:12]}",
        name=name.strip(),
        url=url.strip(),
        authorization=authorization.strip(),
        body=body,
    )
    _webhooks.append(hook)
    _save()
    return hook


def update(
    webhook_id: str,
    *,
    name: str | None = None,
    url: str | None = None,
    authorization: str | None = None,
    body: Any = _UNSET,
) -> Webhook | None:
    """Patch a webhook. ``authorization=None`` leaves the secret unchanged;
    pass ``""`` to clear it. ``body=_UNSET`` leaves the JSON body unchanged;
    pass ``None`` to clear it.
    """
    hook = get(webhook_id)
    if hook is None:
        return None
    if name is not None:
        hook.name = name.strip()
    if url is not None:
        hook.url = url.strip()
    if authorization is not None:
        hook.authorization = authorization.strip()
    if body is not _UNSET:
        hook.body = body
    _save()
    return hook


def remove(webhook_id: str) -> bool:
    _ensure_loaded()
    before = len(_webhooks)
    _webhooks[:] = [w for w in _webhooks if w.id != webhook_id]
    if len(_webhooks) == before:
        return False
    _save()
    return True


def public_dict(hook: Webhook) -> dict[str, Any]:
    """JSON for the HTTP API — never includes the authorization secret."""
    return {
        "id": hook.id,
        "name": hook.name,
        "url": hook.url,
        "has_authorization": bool(hook.authorization),
        "body": hook.body,
    }
