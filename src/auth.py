"""Resolve credentials to principals. No shared DEVICE_TOKEN/channel fallback."""

from pathlib import Path

from auth_policy import Principal
from auth_store import CredentialStore
from config import get_config

_store: CredentialStore | None = None


def get_store() -> CredentialStore:
    global _store
    path = Path(get_config().data_dir) / "authz.json"
    if _store is None or _store.path != path:
        _store = CredentialStore(path)
    return _store


def authenticate(token: object) -> Principal | None:
    return get_store().authenticate(token)


def current(principal: Principal | None) -> bool:
    return get_store().current(principal)
