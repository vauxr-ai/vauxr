"""Version 1 credential schema; provisioning/lifecycle are separate packages.

Only high-entropy generated bearer tokens are supported by this verifier schema.
No plaintext credentials, legacy-token imports, or automatic enrollment.
"""

import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from auth_policy import Principal, Role

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def atomic_private_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream)
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


@dataclass(frozen=True)
class Credential:
    id: str
    role: Role
    subject: str
    verifier: str = field(repr=False)
    enabled: bool = True

    @property
    def generation(self) -> str:
        """Stable across reloads; never expose the authentication verifier itself."""
        return hashlib.sha256(b"vauxr:credential-generation:v1\0" + self.verifier.encode("ascii")).hexdigest()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.role, Role)
            or not isinstance(self.id, str)
            or not _ID.fullmatch(self.id)
            or not isinstance(self.subject, str)
            or not _ID.fullmatch(self.subject)
            or type(self.enabled) is not bool
            or not isinstance(self.verifier, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.verifier)
        ):
            raise ValueError("Invalid credential schema")


def verifier(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_owner_state(state: object) -> None:
    """Reject corrupt owner metadata before publishing any credential snapshot."""
    if state == {}:
        return  # Foundation v1, before owner startup migration.
    if not isinstance(state, dict):
        raise ValueError("Invalid owner state")  # noqa: TRY004
    required = {"version", "mode", "generation"}
    optional = {"verifier", "claim", "claim_expires", "claim_attempts", "pending", "attempts"}
    if (not required <= state.keys() or not state.keys() <= required | optional
            or type(state["version"]) is not int or state["version"] != 1
            or state["mode"] not in ("unclaimed", "recovery", "generated", "environment")
            or not isinstance(state["generation"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", state["generation"])):
        raise ValueError("Invalid owner state")

    def digest(value: object) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    def timestamp(value: object) -> bool:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    active = state["mode"] in ("generated", "environment")
    if active:
        if not digest(state.get("verifier")) or state.keys() - (required | {"verifier", "attempts"}):
            raise ValueError("Invalid owner state")
    elif "verifier" in state:
        raise ValueError("Invalid owner state")
    if "claim" in state and (not digest(state["claim"]) or "pending" in state
                             or "claim_expires" not in state or "claim_attempts" not in state):
        raise ValueError("Invalid owner state")
    if "claim_expires" in state and not timestamp(state["claim_expires"]):
        raise ValueError("Invalid owner state")
    if "claim_attempts" in state and (type(state["claim_attempts"]) is not int
                                      or not 0 <= state["claim_attempts"] <= 5):
        raise ValueError("Invalid owner state")
    if "pending" in state:
        pending = state["pending"]
        if (not isinstance(pending, dict) or pending.keys() != {"verifier", "ack", "expires"}
                or not digest(pending["verifier"]) or not digest(pending["ack"])
                or not timestamp(pending["expires"])):
            raise ValueError("Invalid owner state")
    attempts = state.get("attempts", [])
    if not isinstance(attempts, list) or len(attempts) > 10 or not all(timestamp(at) for at in attempts):
        raise ValueError("Invalid owner state")


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: tuple[Credential, ...] = ()
        self.owner: dict = {}
        self._lock = threading.RLock()
        self._transaction_active = False
        self.load()

    @staticmethod
    def _validate(records: tuple[Credential, ...]) -> None:
        if (
            len({r.id for r in records}) != len(records)
            or len({r.verifier for r in records}) != len(records)
            or len({r.subject for r in records if r.role == Role.OWNER}) > 1
        ):
            raise ValueError("Ambiguous credential schema")
        subjects: dict[str, Role] = {}
        for record in records:
            if subjects.setdefault(record.subject, record.role) != record.role:
                raise ValueError("Ambiguous principal identity")

    def load(self) -> None:
        self.owner = {}
        self.records = ()  # A failed reload never leaves stale access active.
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                data = json.load(stream)
            if (
                not isinstance(data, dict)
                or (set(data) != {"version", "credentials"} if data.get("version") == 1
                 else set(data) != {"version", "credentials", "owner"})
                or type(data["version"]) is not int
                or data["version"] not in (1, 2)
            ):
                raise ValueError
            records = tuple(Credential(**{**row, "role": Role(row["role"])}) for row in data["credentials"])
            self._validate(records)
            validate_owner_state(data.get("owner", {}))
        except (ValueError, TypeError, KeyError):
            raise ValueError("Invalid credential store") from None
        owner = data.get("owner", {})
        if not isinstance(owner, dict):
            raise ValueError("Invalid credential store")  # noqa: TRY004
        self.owner = owner
        self.records = records

    @contextmanager
    def transaction(self) -> Iterator["CredentialStore"]:
        """Serialize read/modify/durable-write across console and server processes.

        Downstream writers MUST build snapshots inside this boundary.
        """
        with self._lock:
            if self._transaction_active:
                yield self
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                self.load()
                self._transaction_active = True
                yield self
            finally:
                self._transaction_active = False
                os.close(fd)

    def save_owner(self, owner: dict) -> None:
        if not self._transaction_active:
            raise RuntimeError("Owner writes require a store transaction")
        validate_owner_state(owner)
        self.owner = owner
        try:
            self.replace(self.records)
        except BaseException:
            self.load()
            raise

    def replace(self, records: tuple[Credential, ...]) -> None:
        """Internal durable schema boundary, not an owner or enrollment API."""
        self._validate(records)
        old = {r.id: r for r in self.records}
        for record in records:
            previous = old.get(record.id)
            if previous and (previous.role, previous.subject, previous.verifier) != (
                record.role,
                record.subject,
                record.verifier,
            ):
                raise ValueError("Credential IDs are immutable; replacement requires a new ID")
        try:
            atomic_private_json(
                self.path, {"version": 2, "credentials": [asdict(r) for r in records], "owner": self.owner}
            )
        except OSError:
            # A directory fsync can fail after rename. Never retain a different
            # authorization snapshot from the file that is now visible.
            self.load()
            raise
        self.records = records

    def authenticate(self, token: object) -> Principal | None:
        if not isinstance(token, str) or not token or len(token) > 512:
            return None
        try:
            digest = verifier(token)
        except UnicodeError:
            return None
        for record in self.records:
            if hmac.compare_digest(digest, record.verifier) and record.enabled:
                return Principal(record.role, record.subject, record.id, record.generation)
        return None

    def current(self, principal: Principal | None) -> bool:
        return principal is not None and any(
            r.enabled
            and (r.id, r.role, r.subject) == (principal.credential_id, principal.role, principal.subject)
            and r.generation == principal.credential_generation
            for r in self.records
        )
