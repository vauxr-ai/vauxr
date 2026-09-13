"""Owner contract v1. Random credentials only; console authority is local OS access."""

import hmac
import ipaddress
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from auth_policy import Principal, Role
from auth_store import CredentialStore, verifier

TOKEN_PATTERN = re.compile(r"vx_op_[A-Za-z0-9_-]{43}\Z")
CLAIM_SECONDS = 300
SESSION_SECONDS = 43200


def generate_token() -> str:
    return "vx_op_" + secrets.token_urlsafe(32)


def environment_token() -> str | None:
    token = os.environ.get("OPERATOR_TOKEN")
    if token is not None and not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("OPERATOR_TOKEN must be generated with vauxr-owner generate-token; empty is invalid")
    return token


def trusted_origin(value: str, scheme: str = "https") -> str:
    """Require canonical ASCII DNS/IP authority; never derive trust from a request."""
    parsed = urlsplit(value)
    error = "Owner origin must be an exact canonical " + scheme.upper() + " origin"
    if (parsed.scheme != scheme or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or value != f"{scheme}://{parsed.netloc}"):
        raise ValueError(error)
    host = parsed.hostname
    if "%" in host:
        raise ValueError(error)
    try:
        address = ipaddress.ip_address(host)
        canonical_host = f"[{address}]" if address.version == 6 else str(address)
    except ValueError:
        # Browsers interpret a numeric final label as IPv4, including shortened,
        # integer, octal and hexadecimal forms. Only ipaddress's canonical dotted
        # decimal above is allowed; these must never fall through as DNS names.
        if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", host.rsplit(".", 1)[-1]):
            raise ValueError(error) from None
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                      for label in host.split(".")):
            raise ValueError(error) from None
        canonical_host = host
    port = parsed.port  # Also rejects invalid/out-of-range ports.
    authority = canonical_host + (f":{port}" if port is not None else "")
    # Browsers omit explicit default ports from both Host and Origin.
    if parsed.netloc != authority or port in (0, {"http": 80, "https": 443}[scheme]):
        raise ValueError(error)
    return value


def configured_origin() -> str:
    """Presence of TLS settings selects TLS, even if empty or malformed."""
    if "OWNER_HTTPS_ORIGIN" in os.environ or "OWNER_TRUSTED_PROXIES" in os.environ:
        origin = trusted_origin(os.environ.get("OWNER_HTTPS_ORIGIN", ""))
        raw = os.environ.get("OWNER_TRUSTED_PROXIES")
        if raw is not None:
            # Empty lists/entries are configuration mistakes, not LAN selection.
            for value in raw.split(","):
                ipaddress.ip_network(value.strip())
        return origin
    return trusted_origin(os.environ.get("OWNER_HTTP_ORIGIN", "http://localhost:8080"), "http")


class OwnerError(Exception):
    """Only fixed public error codes may cross this boundary."""


@dataclass
class Session:
    generation: str
    expires: float
    csrf: str = field(repr=False)


class OwnerAuth:
    def __init__(self, store: CredentialStore, override: str | None = None, *, origin: str = "") -> None:
        if override is not None and not TOKEN_PATTERN.fullmatch(override):
            raise ValueError("Invalid OPERATOR_TOKEN; use vauxr-owner generate-token")
        self.store = store
        self.override = verifier(override) if override is not None else None
        self.sessions: dict[str, Session] = {}
        self._origin = origin

    def bind_origin(self, origin: str) -> None:
        """Transport changes discard all sessions, including an A -> B -> A transition."""
        if origin != self._origin:
            self.sessions.clear()
            self._origin = origin

    def _state(self) -> dict:
        state = self.store.owner
        if state and (state.get("version") != 1 or state.get("mode") not in
                      {"unclaimed", "generated", "environment", "recovery"}
                      or not isinstance(state.get("generation"), str)):
            raise ValueError("Invalid owner state")
        return state

    def initialize(self) -> None:
        with self.store.transaction():
            state = self._state()
            if self.override is not None:
                if state.get("mode") != "environment" or state.get("verifier") != self.override:
                    self.store.save_owner({"version": 1, "mode": "environment",
                                           "generation": secrets.token_hex(16), "verifier": self.override})
            elif state.get("mode") == "environment":
                self.store.save_owner({"version": 1, "mode": "recovery",
                                       "generation": secrets.token_hex(16)})
            elif not state:
                self.store.save_owner({"version": 1, "mode": "unclaimed",
                                       "generation": secrets.token_hex(16)})

    def status(self) -> dict:
        with self.store.transaction():
            state = self._state()
            return {"version": 1, "state": state.get("mode", "unclaimed"),
                    "environment_managed": state.get("mode") == "environment"}

    def console_claim(self, recover: bool = False) -> str:
        with self.store.transaction():
            state = self._state()
            if self.override is not None or state.get("mode") == "environment":
                raise OwnerError("Replace the authoritative OPERATOR_TOKEN environment value and restart; "
                                 "or remove it and restart, then run recover. "
                                 "Persisted rotation cannot override it.")
            if not recover and state.get("mode", "unclaimed") != "unclaimed":
                raise OwnerError("Already claimed; use the explicit recover command")
            code = secrets.token_urlsafe(24)
            self.store.save_owner({"version": 1, "mode": "recovery" if recover else "unclaimed",
                                   "generation": secrets.token_hex(16), "claim": verifier(code),
                                   "claim_expires": time.time() + CLAIM_SECONDS, "claim_attempts": 0})
            self.sessions.clear()
            return code

    def rate_limit(self) -> None:
        with self.store.transaction():
            state = dict(self._state())
            now = time.time()
            attempts = [at for at in state.get("attempts", []) if now - at < 60]
            if len(attempts) >= 10:
                raise OwnerError("rate_limited")
            state["attempts"] = [*attempts, now]
            self.store.save_owner(state)

    @staticmethod
    def _matches(secret: object, digest: object) -> bool:
        if not isinstance(secret, str) or not 0 < len(secret) <= 512 or not isinstance(digest, str):
            return False
        try:
            return hmac.compare_digest(verifier(secret), digest)
        except UnicodeError:
            return False

    def claim(self, code: object) -> dict:
        with self.store.transaction():
            state = dict(self._state())
            valid = (state.get("mode") in {"unclaimed", "recovery"}
                     and state.get("claim_expires", 0) > time.time()
                     and state.get("claim_attempts", 0) < 5
                     and self._matches(code, state.get("claim")))
            if not valid:
                if "claim" in state:
                    state["claim_attempts"] = min(5, state.get("claim_attempts", 0) + 1)
                    self.store.save_owner(state)
                raise OwnerError("invalid_claim")
            token, acknowledgement = generate_token(), secrets.token_urlsafe(32)
            state.pop("claim")
            state["pending"] = {"verifier": verifier(token), "ack": verifier(acknowledgement),
                                "expires": time.time() + CLAIM_SECONDS}
            self.store.save_owner(state)
            return {"version": 1, "operator_token": token, "save_acknowledgement": acknowledgement,
                    "expires_in": CLAIM_SECONDS, "save_required": True}

    def acknowledge(self, acknowledgement: object, saved: object) -> None:
        with self.store.transaction():
            state = self._state()
            pending = state.get("pending", {})
            if (saved is not True or pending.get("expires", 0) <= time.time()
                    or not self._matches(acknowledgement, pending.get("ack"))):
                raise OwnerError("invalid_acknowledgement")
            self.store.save_owner({"version": 1, "mode": "generated", "generation": state["generation"],
                                   "verifier": pending["verifier"]})

    def login(self, token: object) -> tuple[str, Session]:
        with self.store.transaction():
            state = self._state()
            if state.get("mode") not in {"generated", "environment"} or not self._matches(
                    token, state.get("verifier")):
                raise OwnerError("invalid_login")
            now = time.time()
            self.sessions = {key: value for key, value in self.sessions.items()
                             if value.expires > now and value.generation == state["generation"]}
            if len(self.sessions) >= 100:
                raise OwnerError("rate_limited")
            cookie = secrets.token_urlsafe(32)
            session = Session(state["generation"], now + SESSION_SECONDS, secrets.token_urlsafe(32))
            self.sessions[verifier(cookie)] = session
            return cookie, session

    def session(self, cookie: str) -> tuple[Principal, Session] | None:
        if not cookie or len(cookie) > 512:
            return None
        with self.store.transaction():
            state = self._state()
            session = self.sessions.get(verifier(cookie))
            if session is None:
                return None
            if session.expires <= time.time() or session.generation != state.get("generation"):
                self.sessions.pop(verifier(cookie), None)
                return None
            return Principal(Role.OWNER, "owner", session.generation), session

    def logout(self, cookie: str) -> None:
        self.sessions.pop(verifier(cookie), None)
