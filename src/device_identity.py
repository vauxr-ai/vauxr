"""Device identity (Ed25519 keypair) + signed connect payloads.

Port of `src/device-identity.ts`. The disk format must round-trip with the
Node version: the same JSON file (`vauxr-identity.json`) with the same
base64/base64url-encoded fields and the same v3 connect signature scheme.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import TypedDict

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

log = logging.getLogger("vauxr.device_identity")

# SPKI prefix for Ed25519 keys — 12 bytes before the raw 32-byte key.
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")

DATA_FILE = "vauxr-identity.json"


class DeviceIdentity(TypedDict):
    publicKeyRaw: str  # base64url, no padding — raw 32-byte Ed25519 public key
    privateKeyDer: str  # base64 (standard) — PKCS8 DER private key
    fingerprint: str  # hex SHA-256 of the raw 32-byte public key


class StoredData(TypedDict, total=False):
    identity: DeviceIdentity
    deviceToken: str


_cached: StoredData | None = None


def _b64url_no_pad(buf: bytes) -> str:
    return base64.urlsafe_b64encode(buf).rstrip(b"=").decode("ascii")


def _data_file_path(data_dir: str) -> str:
    return os.path.join(data_dir, DATA_FILE)


def _generate_identity() -> DeviceIdentity:
    priv = Ed25519PrivateKey.generate()
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_der = priv.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    fingerprint = hashlib.sha256(raw_pub).hexdigest()
    return DeviceIdentity(
        publicKeyRaw=_b64url_no_pad(raw_pub),
        privateKeyDer=base64.b64encode(priv_der).decode("ascii"),
        fingerprint=fingerprint,
    )


def load_or_create_identity(data_dir: str) -> StoredData:
    global _cached
    if _cached is not None:
        return _cached

    path = _data_file_path(data_dir)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _cached = json.load(f)
        log.info("Loaded identity: %s", _cached["identity"]["fingerprint"])
        return _cached

    identity = _generate_identity()
    _cached = StoredData(identity=identity)
    os.makedirs(data_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_cached, f, indent=2)
    log.info("Generated new identity: %s", identity["fingerprint"])
    return _cached


def save_device_token(data_dir: str, device_token: str) -> None:
    global _cached
    data = load_or_create_identity(data_dir)
    data["deviceToken"] = device_token
    _cached = data
    with open(_data_file_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    log.info("Device token saved")


def get_device_token(data_dir: str) -> str | None:
    return load_or_create_identity(data_dir).get("deviceToken")


def reset_cache() -> None:
    """Clear the module-level cache. Test helper."""
    global _cached
    _cached = None


@dataclass(frozen=True)
class SignParams:
    nonce: str
    token: str
    client_id: str
    client_mode: str
    role: str
    scopes: list[str]
    platform: str
    device_family: str


@dataclass(frozen=True)
class SignedConnect:
    signature: str
    signed_at: int


def sign_connect_payload(data_dir: str, params: SignParams) -> SignedConnect:
    """Build a v3 connect signature.

    Payload format (must stay byte-for-byte identical to the Node port):
      v3|deviceId|clientId|clientMode|role|scopes|signedAt|token|nonce|platform|deviceFamily
    """
    import time

    data = load_or_create_identity(data_dir)
    identity = data["identity"]

    priv_der = base64.b64decode(identity["privateKeyDer"])
    priv = serialization.load_der_private_key(priv_der, password=None)
    assert isinstance(priv, Ed25519PrivateKey)

    signed_at = int(time.time() * 1000)
    payload = "|".join(
        [
            "v3",
            identity["fingerprint"],
            params.client_id,
            params.client_mode,
            params.role,
            ",".join(params.scopes),
            str(signed_at),
            params.token,
            params.nonce,
            params.platform.lower().strip(),
            params.device_family.lower().strip(),
        ]
    )
    signature = priv.sign(payload.encode("utf-8"))
    return SignedConnect(signature=_b64url_no_pad(signature), signed_at=signed_at)
