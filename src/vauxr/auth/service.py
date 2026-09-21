"""Resolve credentials to principals. No shared DEVICE_TOKEN/agent fallback."""

from pathlib import Path

from vauxr.auth.policy import Principal
from vauxr.auth.store import CredentialStore
from vauxr.config import get_config

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
