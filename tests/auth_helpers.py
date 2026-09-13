"""Explicit schema fixtures; never infer enrollment from DEVICE_TOKEN."""

from auth import get_store
from auth_policy import Role
from auth_store import Credential, verifier


def seed(token: str, role: Role, subject: str) -> None:
    store = get_store()
    record = Credential(subject + "-credential", role, subject, verifier(token))
    store.replace(tuple(r for r in store.records if r.id != record.id) + (record,))
