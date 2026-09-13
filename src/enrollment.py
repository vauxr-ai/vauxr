"""Bounded enrollment v1. Key possession plus human confirmation, never attestation."""

import copy
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from auth_policy import Operation, PairApprovalResult, Principal, Role, allowed
from auth_store import Credential, CredentialStore, verifier
from enrollment_schema import BINDING, MAX_REQUESTS, TTL, hex_string

log = logging.getLogger("vauxr.enrollment")
TERMINAL = {"consumed", "denied", "cancelled", "failed", "expired", "stale"}
Resolve = Callable[[], Principal | None]


class EnrollmentError(Exception):
    """Fixed, secret-free public error code."""


def transcript(row: dict, action: str) -> bytes:
    """Exact v1 wire transcript: fixed-position compact ASCII JSON array."""
    return json.dumps(
        ["vauxr-enrollment", action, *[row[key] for key in BINDING]], ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


def code_digest(row: dict, code: str) -> str:
    return hashlib.sha256(transcript(row, "code") + b"\n" + code.encode("ascii")).hexdigest()


class Enrollment:
    def __init__(self, store: CredentialStore, origin: str) -> None:
        self.store = store
        self.origin = origin

    def _state(self) -> dict:
        state = copy.deepcopy(self.store.enrollment) or {
            "version": 1,
            "server_id": secrets.token_hex(16),
            "requests": {},
            "attempts": [],
        }
        changed = False
        for row in state["requests"].values():
            if row["state"] not in TERMINAL and self._status(row) == "stale":
                row["state"] = "stale"
                if not row["code_hash"]:
                    row["code_hash"] = "0" * 64
                changed = True
        if changed:
            self.store.save_enrollment(state)
        return state

    def initialize(self) -> None:
        """Persist trust/authority invalidation even if no old request is examined."""
        with self.store.transaction():
            self._state()

    def rate_limit(self) -> None:
        """Charge before parsing even malformed HTTP bodies; global, durable, bounded."""
        with self.store.transaction():
            state = self._state()
            now = time.time()
            attempts = [at for at in state["attempts"] if now - at < 60]
            if len(attempts) >= 60:
                raise EnrollmentError("rate_limited")
            state["attempts"] = [*attempts, now]
            self.store.save_enrollment(state)

    def _actor_current(self, actor: dict | None) -> bool:
        if actor is None:
            return False
        principal = Principal(**{**actor, "role": Role(actor["role"])})
        if principal.role == Role.OWNER:
            return self.store.owner.get("mode") in (
                "generated",
                "environment",
            ) and principal.credential_id == self.store.owner.get("generation")
        return self.store.current(principal)

    def _status(self, row: dict) -> str:
        if row["state"] in TERMINAL:
            return row["state"]
        if row["expires_at"] <= time.time():
            return "expired"
        if (
            row["owner_generation"] != self.store.owner.get("generation")
            or row["origin"] != self.origin
            or any(
                row[field] is not None and not self._actor_current(row[field])
                for field in ("initiator", "approver")
            )
        ):
            return "stale"
        return row["state"]

    def _controller(self, resolve: Resolve, kind: str) -> Principal:
        # Called only under the shared lock. Owner resolver must recheck the cookie,
        # expiry and epoch here, not pass an earlier middleware principal.
        principal = resolve()
        if principal is None:
            raise EnrollmentError("unauthorized")
        if (
            principal.role not in (Role.OWNER, Role.INTEGRATION)
            or (principal.role == Role.INTEGRATION and not self.store.current(principal))
            or (principal.role == Role.OWNER and not self._actor_current(asdict(principal)))
            or (kind == "browser" and principal.role != Role.OWNER)
        ):
            raise EnrollmentError("forbidden")
        return principal

    def _failure(self, state: dict, row: dict) -> None:
        if self._status(row) in TERMINAL:
            raise EnrollmentError("invalid_proof")
        row["attempts"] = min(5, row["attempts"] + 1)
        if row["attempts"] == 5:
            row["state"] = "failed"
            if not row["code_hash"]:
                row["code_hash"] = "0" * 64
        self.store.save_enrollment(state)
        raise EnrollmentError("invalid_proof")

    def _signature(self, state: dict, row: dict, action: str, signature: object) -> None:
        if not hex_string(signature, 128):
            self._failure(state, row)
        try:
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(row["public_key"])).verify(
                bytes.fromhex(signature), transcript(row, action)
            )
        except (InvalidSignature, ValueError):
            self._failure(state, row)

    def execute(self, action: str, body: dict, resolve: Resolve = lambda: None) -> dict:
        fields = {
            "request": {"kind", "public_key", "display_name"},
            "prove": {"request_id", "signature"},
            "redeem": {"request_id", "signature"},
            "cancel": {"request_id", "signature"},
            "status": {"request_id", "signature"},
            "initiate": {"request_id", "code"},
            "approve": {"request_id", "code"},
            "deny": {"request_id"},
            "list": set(),
        }
        if action not in fields or not isinstance(body, dict) or body.keys() != fields[action]:
            raise EnrollmentError("invalid_request")
        with self.store.transaction():
            state = self._state()
            if action == "request":
                if body["kind"] == "browser":
                    self._controller(resolve, "browser")
                return self._request(state, body)
            if action == "list":
                principal = self._controller(resolve, "physical")
                rows = [
                    row
                    for row in state["requests"].values()
                    if principal.role == Role.OWNER or row["kind"] == "physical"
                ]
                return {"version": 1, "requests": [self._public(row) for row in rows]}
            if not hex_string(body["request_id"], 32):
                raise EnrollmentError("invalid_request")
            row = state["requests"].get(body["request_id"])
            if row is None:
                raise EnrollmentError("not_found")
            principal = None
            if action in ("initiate", "approve", "deny"):
                principal = self._controller(resolve, row["kind"])
            status = self._status(row)
            if action == "status":
                # A terminal status is readable with the key; never exposes code/token.
                self._signature(state, row, action, body["signature"])
                return self._public(row)
            if status in TERMINAL:
                raise EnrollmentError("unavailable")
            if action in ("prove", "redeem", "cancel"):
                self._signature(state, row, action, body["signature"])
            if action == "prove" and status == "challenge":
                code = f"{secrets.randbelow(100_000_000):08d}"
                row.update(state="ready", code_hash=code_digest(row, code))
                self.store.save_enrollment(state)
                log.info("enrollment key proved")
                return {"version": 1, "status": "ready", "code": code, "expires_at": row["expires_at"]}
            if action in ("initiate", "approve"):
                expected = "ready" if action == "initiate" else "initiated"
                if status != expected:
                    raise EnrollmentError("unavailable")
                code = body["code"]
                if (
                    not isinstance(code, str)
                    or not re.fullmatch(r"[0-9]{8}", code)
                    or not hmac.compare_digest(code_digest(row, code), row["code_hash"])
                ):
                    self._failure(state, row)
                # physical_verified is the policy's human/code boundary, NOT a
                # claim that an anonymous request proves a hardware button press.
                operation = Operation.PAIR_INITIATE if action == "initiate" else Operation.PAIR_APPROVE
                permitted = (
                    allowed(principal, operation, physical_verified=True)
                    if row["kind"] == "physical"
                    else principal.role == Role.OWNER
                )
                if not permitted:
                    raise EnrollmentError("forbidden")
                self._unowned(row)
                row["initiator" if action == "initiate" else "approver"] = asdict(principal)
                row["state"] = "initiated" if action == "initiate" else "approved"
            elif action == "redeem" and status == "approved":
                self._unowned(row)
                token = "vx_dev_" + secrets.token_urlsafe(32)
                credential = Credential(secrets.token_hex(16), Role.DEVICE, row["device_id"], verifier(token))
                row["state"] = "consumed"
                self.store.save_enrollment(state, (*self.store.records, credential))
                log.info("enrollment consumed")
                return {
                    "version": 1,
                    "status": "consumed",
                    "device_id": row["device_id"],
                    "credential_id": credential.id,
                    "device_token": token,
                }
            elif action in ("cancel", "deny"):
                row["state"] = "cancelled" if action == "cancel" else "denied"
                # Terminal challenge records have no code; use a non-secret sentinel digest.
                if not row["code_hash"]:
                    row["code_hash"] = "0" * 64
            else:
                raise EnrollmentError("unavailable")
            self.store.save_enrollment(state)
            log.info("enrollment %s", row["state"])
            return PairApprovalResult(row["state"], row["device_id"]).public_dict()

    def _public(self, row: dict) -> dict:
        return {
            "request_id": row["request_id"],
            "device_id": row["device_id"],
            "kind": row["kind"],
            "display_name": row["display_name"],
            "status": self._status(row),
            "expires_at": row["expires_at"],
        }

    def _unowned(self, row: dict) -> None:
        if len(self.store.records) >= 1024:
            raise EnrollmentError("capacity")
        # Disabled records count too. No overwrite, rotation or reset side effect.
        if any(record.subject == row["device_id"] for record in self.store.records):
            raise EnrollmentError("already_owned")

    def _request(self, state: dict, body: dict) -> dict:
        if (
            body["kind"] not in ("physical", "browser")
            or not hex_string(body["public_key"], 64)
            or not isinstance(body["display_name"], str)
            or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,64}", body["display_name"])
        ):
            raise EnrollmentError("invalid_request")
        if (
            not self.origin
            or len(self.origin) > 256
            or not self.origin.isascii()
            or self.store.owner.get("mode") not in ("generated", "environment")
        ):
            raise EnrollmentError("unavailable")
        now = int(time.time())
        # Retain terminal results until original expiry; never evict live requests.
        state["requests"] = {key: row for key, row in state["requests"].items() if row["expires_at"] > now}
        if len(state["requests"]) >= MAX_REQUESTS:
            raise EnrollmentError("capacity")
        row = {
            "version": 1,
            "request_id": secrets.token_hex(16),
            "server_id": state["server_id"],
            "origin": self.origin,
            **body,
            "nonce": secrets.token_hex(32),
            "expires_at": now + TTL,
            "owner_generation": self.store.owner["generation"],
            "state": "challenge",
            "attempts": 0,
            "code_hash": "",
            "initiator": None,
            "approver": None,
            "device_id": "dev_" + hashlib.sha256(bytes.fromhex(body["public_key"])).hexdigest(),
        }
        self._unowned(row)
        if any(
            other["device_id"] == row["device_id"] and self._status(other) not in TERMINAL
            for other in state["requests"].values()
        ):
            raise EnrollmentError("conflict")
        state["requests"][row["request_id"]] = row
        self.store.save_enrollment(state)
        log.info("enrollment challenge created")
        return {key: row[key] for key in BINDING}
