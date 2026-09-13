"""Version 1 credential schema; provisioning/lifecycle are separate packages.

Only high-entropy generated bearer tokens are supported by this verifier schema.
No plaintext credentials, legacy-token imports, or automatic enrollment.
"""

import hashlib
import hmac
import json
import os
import re
import tempfile
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


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: tuple[Credential, ...] = ()
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
        self.records = ()  # A failed reload never leaves stale access active.
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                data = json.load(stream)
            if (
                set(data) != {"version", "credentials"}
                or type(data["version"]) is not int
                or data["version"] != 1
            ):
                raise ValueError
            records = tuple(Credential(**{**row, "role": Role(row["role"])}) for row in data["credentials"])
            self._validate(records)
        except (ValueError, TypeError, KeyError):
            raise ValueError("Invalid credential store") from None
        self.records = records

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
            atomic_private_json(self.path, {"version": 1, "credentials": [asdict(r) for r in records]})
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
