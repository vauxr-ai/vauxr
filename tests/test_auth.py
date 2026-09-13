"""Authorization matrix and durable identity invariants."""

import json
import os
from dataclasses import replace

import pytest

from auth_policy import Operation as O
from auth_policy import PairApprovalResult, Principal, Role, allowed
from auth_store import Credential, CredentialStore, verifier

# Explicit expected grants, independent from production sets. Every new enum value
# must get a reviewed row (the completeness assertion prevents silent coverage gaps).
MATRIX = {
    O.DEVICES_LIST: "oi",
    O.DEVICE_CONFIG: "o",
    O.ANNOUNCE: "oi",
    O.CONTROL: "oi",
    O.PLAYBACK: "oi",
    O.FIRMWARE_INITIATE: "oi",
    O.FIRMWARE_READ: "od",
    O.FIRMWARE_PUBLISH: "o",
    O.CHANNEL_LIST: "o",
    O.CHANNEL_CONFIG: "o",
    O.CREDENTIAL_CREATE: "o",
    O.CREDENTIAL_DISCLOSE: "o",
    O.CREDENTIAL_ROTATE: "o",
    O.CREDENTIAL_REVOKE: "o",
    O.SERVER_MANAGE: "o",
    O.OWNER_ADMIN: "o",
    O.WEBHOOK_CONFIG: "o",
    O.PAIR_INITIATE: "oi",
    O.PAIR_APPROVE: "oi",
    O.DEVICE_CONNECT: "d",
    O.DEVICE_AUDIO: "d",
    O.DEVICE_CONTROL: "d",
    O.DEVICE_BUTTON: "d",
    O.REALTIME_OFFER: "d",
    O.CHANNEL_CONNECT: "i",
    O.VOICE_RESPONSE: "i",
}


@pytest.mark.parametrize("operation,grants", MATRIX.items())
@pytest.mark.parametrize(
    "role,key", [(Role.OWNER, "o"), (Role.INTEGRATION, "i"), (Role.DEVICE, "d"), (None, "-")]
)
def test_policy_matrix(operation, grants, role, key):
    assert set(MATRIX) == set(O)
    principal = Principal(role, "speaker", "credential") if role else None
    assert allowed(principal, operation, resource="speaker", physical_verified=True) == (key in grants)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("operation", [O.PAIR_INITIATE, O.PAIR_APPROVE])
def test_pairing_requires_server_verified_physical_context(role, operation):
    assert not allowed(Principal(role, "subject", "id"), operation)
    assert PairApprovalResult("approved", "speaker").public_dict() == {
        "status": "approved",
        "device_id": "speaker",
    }


@pytest.mark.parametrize(
    "operation", [O.DEVICE_CONNECT, O.DEVICE_AUDIO, O.DEVICE_CONTROL, O.DEVICE_BUTTON, O.REALTIME_OFFER]
)
@pytest.mark.parametrize("resource", [None, "", "other"])
def test_device_cannot_cross_identity(operation, resource):
    assert not allowed(Principal(Role.DEVICE, "speaker", "id"), operation, resource=resource)


def test_unknown_operation_denied():
    assert not allowed(Principal(Role.OWNER, "owner", "id"), "future.admin")


def record(role=Role.DEVICE, subject="speaker", token="generated-test-secret"):
    return Credential(subject + "-id", role, subject, verifier(token))


def test_persistence_restart_permissions_and_redaction(tmp_path):
    path = tmp_path / "authz.json"
    store = CredentialStore(path)
    assert store.authenticate("legacy") is None
    store.replace((record(),))
    assert path.stat().st_mode & 0o777 == 0o600
    assert "generated-test-secret" not in path.read_text()
    assert record().verifier not in repr(record())
    restarted = CredentialStore(path)
    assert restarted.authenticate("generated-test-secret") == Principal(Role.DEVICE, "speaker", "speaker-id")
    os.chmod(path, 0o644)
    restarted.load()
    assert path.stat().st_mode & 0o777 == 0o600
    restarted.replace((replace(record(), enabled=False),))
    assert CredentialStore(path).authenticate("generated-test-secret") is None


def test_failed_atomic_write_keeps_previous_identity(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "authz.json")
    store.replace((record(),))

    def fail(*args):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.replace((replace(record(), enabled=False),))
    assert store.authenticate("generated-test-secret") is not None
    assert CredentialStore(store.path).authenticate("generated-test-secret") is not None
    assert list(tmp_path.iterdir()) == [store.path]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 2, "credentials": []},
        {"version": 1, "credentials": [{"role": "admin"}]},
        {"version": 1, "credentials": None},
    ],
)
def test_corruption_clears_previous_access(tmp_path, payload):
    store = CredentialStore(tmp_path / "authz.json")
    store.replace((record(),))
    store.path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Invalid credential store"):
        store.load()
    assert store.authenticate("generated-test-secret") is None


def test_ambiguous_and_rebound_credentials_rejected(tmp_path):
    store = CredentialStore(tmp_path / "authz.json")
    store.replace((record(),))
    for rows in [
        (record(), record()),
        (record(), replace(record(), id="second")),
        (replace(record(), subject="victim"),),
        (replace(record(), role=Role.OWNER),),
        (record(), record(Role.INTEGRATION, "speaker", "another-token")),
    ]:
        with pytest.raises(ValueError):
            store.replace(rows)


@pytest.mark.parametrize("token", [None, "", [], {}, 1, "x" * 513, "wrong", "\ud800"])
def test_malformed_credentials_are_rejected(tmp_path, token):
    store = CredentialStore(tmp_path / "authz.json")
    assert store.authenticate(token) is None


def test_directory_sync_failure_does_not_retain_stale_grant(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "authz.json")
    store.replace((record(),))
    original = os.fsync
    calls = 0

    def fail_directory(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory sync failure")
        original(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError):
        store.replace((replace(record(), enabled=False),))
    assert store.authenticate("generated-test-secret") is None
    assert CredentialStore(store.path).authenticate("generated-test-secret") is None
