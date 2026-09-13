"""Owner-approved software enrollment with one-time delivery and durable-save ACK."""

import copy
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import replace

from auth_policy import Role
from auth_store import Credential, CredentialStore, verifier
from enrollment import Enrollment, EnrollmentError, Resolve
from enrollment_schema import hex_string
from integration_schema import LIMIT, PUBLIC, TERMINAL, display_name, empty_state
from lifecycle import Lifecycle

log = logging.getLogger("vauxr.integration")
TTL = 300


def user_code(request_id: str, secret: str, expires_at: int) -> str:
    binding = bytes.fromhex(request_id + secret) + str(expires_at).encode("ascii")
    return hashlib.sha256(b"vauxr-integration-code-v1\0" + binding).hexdigest()[:8].upper()


class Integration:
    def __init__(self, store: CredentialStore, origin: str) -> None:
        self.store = store
        self.origin = origin

    @staticmethod
    def public(row: dict) -> dict:
        return {"version": 1, **{key: row[key] for key in PUBLIC}}

    def _save(self, state: dict, records: tuple[Credential, ...] | None = None) -> None:
        previous = self.store.integration.get("requests", {})
        self.store.integration = state
        try:
            self.store.replace(self.store.records if records is None else records)
        except BaseException:
            self.store.load()
            raise
        for key, row in state["requests"].items():
            if previous.get(key, {}).get("state") != row["state"]:
                log.info("integration enrollment %s", row["state"])

    def sweep(self) -> None:
        with self.store.transaction():
            state = copy.deepcopy(self.store.integration) or empty_state()
            changed = bool(state["active_channel"]) and not any(
                row["channel_id"] == state["active_channel"] and self.store.integration_channel_valid(row)
                for row in state["requests"].values()
            )
            for row in state["requests"].values():
                if row["state"] == "completed":
                    matching = [r for r in self.store.records if r.subject == row["channel_id"]]
                    if matching and all(r.verifier in self.store.lifecycle.get("blocked", []) for r in matching):
                        row["state"] = "revoked"
                        changed = True
                    continue
                if row["state"] in TERMINAL:
                    continue
                credential = next((r for r in self.store.records if r.id == row["credential_id"]), None)
                if credential and credential.verifier in self.store.lifecycle.get("blocked", []):
                    row["state"] = "revoked"
                elif row["expires_at"] <= time.time():
                    row["state"] = "expired"
                elif (row["owner_generation"] != self.store.owner.get("generation")
                      or row["origin"] != self.origin):
                    row["state"] = "stale"
                else:
                    continue
                changed = True
                if credential:
                    self._retire(credential)
            if changed:
                self._save(state)

    def _retire(self, credential: Credential) -> None:
        lifecycle = Lifecycle(self.store, self.origin)
        state = lifecycle._state()
        # Pending enrollment credentials are disabled and cannot have approvals.
        self.store.records = lifecycle._retire(state, {credential.id}, self.store.records)
        self.store.lifecycle = state

    def execute(self, action: str, body: dict, resolve: Resolve = lambda: None) -> dict:
        private = {"request_id", "request_secret"}
        fields = {"request": private | {"origin", "display_name", "expires_at"}, "status": private,
                  "deliver": private, "cancel": private, "ack": private | {"credential", "saved"},
                  "list": set(), "approve": {"request_id", "user_code"}, "deny": {"request_id"}}
        if action not in fields or not isinstance(body, dict) or body.keys() != fields[action]:
            raise EnrollmentError("invalid_request")
        if action != "list" and not hex_string(body["request_id"], 32):
            raise EnrollmentError("invalid_request")
        native = action in ("request", "status", "deliver", "cancel", "ack")
        if native and not hex_string(body["request_secret"], 64):
            raise EnrollmentError("invalid_request")
        with self.store.transaction():
            self.sweep()
            state = copy.deepcopy(self.store.integration) or empty_state()
            if not native:
                principal = resolve()
                if principal is None:
                    raise EnrollmentError("unauthorized")
                if (principal.role != Role.OWNER or principal.subject != "owner"
                        or principal.credential_id != self.store.owner.get("generation")
                        or self.store.owner.get("mode") not in ("generated", "environment")):
                    raise EnrollmentError("forbidden")
            if action == "list":
                return {"version": 1, "requests": [self.public(r) for r in state["requests"].values()]}
            row = state["requests"].get(body["request_id"])
            if action == "request":
                if (body["origin"] != self.origin or not display_name(body["display_name"])
                        or type(body["expires_at"]) is not int):
                    raise EnrollmentError("invalid_request")
                if row is None:
                    if not time.time() < body["expires_at"] <= time.time() + TTL:
                        raise EnrollmentError("unavailable")
                    # Expired unissued requests cost no permanent credential/revoke history.
                    # Their absolute client deadlines prevent replay after pruning.
                    state["requests"] = {key: r for key, r in state["requests"].items()
                                         if r["credential_id"] or r["expires_at"] > time.time()}
                    if sum(not r["credential_id"] for r in state["requests"].values()) >= 64:
                        raise EnrollmentError("capacity")
                    if self.store.owner.get("mode") not in ("generated", "environment"):
                        raise EnrollmentError("unavailable")
                    if len(state["requests"]) >= LIMIT:
                        raise EnrollmentError("capacity")
                    # Initialize the shared server identifier before binding the request.
                    enrollment = Enrollment(self.store, self.origin)._state()
                    self.store.enrollment = enrollment
                    code = user_code(body["request_id"], body["request_secret"], body["expires_at"])
                    row = {"request_id": body["request_id"], "server_id": enrollment["server_id"],
                           "origin": self.origin, "channel_id": "int_" + body["request_id"],
                           "display_name": body["display_name"], "expires_at": body["expires_at"],
                           "created_at": time.time(), "state": "pending", "attempts": 0,
                           "secret_hash": verifier(body["request_secret"]), "code_hash": verifier(code),
                           "owner_generation": self.store.owner["generation"], "credential_id": ""}
                    state["requests"][body["request_id"]] = row
                    self._save(state)
            if row is None:
                raise EnrollmentError("not_found")
            if native and not hmac.compare_digest(row["secret_hash"], verifier(body["request_secret"])):
                raise EnrollmentError("unauthorized")
            if action == "request":
                if (row["display_name"] != body["display_name"] or row["origin"] != body["origin"]
                        or row["expires_at"] != body["expires_at"]):
                    raise EnrollmentError("conflict")
                return {**self.public(row), "user_code": user_code(body["request_id"], body["request_secret"], body["expires_at"])}
            if action == "status":
                return self.public(row)
            if action == "ack":
                if (body["saved"] is not True or not isinstance(body["credential"], str)
                        or len(body["credential"]) > 512):
                    raise EnrollmentError("invalid_ack")
                credential = next((r for r in self.store.records if r.id == row["credential_id"]), None)
                if (credential is None or not hmac.compare_digest(credential.verifier, verifier(body["credential"]))
                        or credential.verifier in self.store.lifecycle.get("blocked", [])):
                    raise EnrollmentError("invalid_ack")
                if row["state"] == "completed":
                    return self.public(row)
                if row["state"] != "delivered":
                    raise EnrollmentError("unavailable")
                row["state"] = "completed"
                self._save(state, tuple(replace(r, enabled=True) if r.id == credential.id else r
                                        for r in self.store.records))
                return self.public(row)
            if action == "approve":
                if row["state"] not in ("pending", "approved"):
                    raise EnrollmentError("unavailable")
                code = body["user_code"]
                if (not isinstance(code, str) or len(code) != 8
                        or not hmac.compare_digest(row["code_hash"], verifier(code))):
                    row["attempts"] += 1
                    if row["attempts"] >= 5:
                        row["state"] = "failed"
                    self._save(state)
                    raise EnrollmentError("invalid_code")
                row["state"] = "approved"
            elif action in ("deny", "cancel"):
                target = "denied" if action == "deny" else "cancelled"
                if row["state"] == target:
                    return self.public(row)
                if row["state"] in TERMINAL:
                    raise EnrollmentError("unavailable")
                row["state"] = target
                credential = next((r for r in self.store.records if r.id == row["credential_id"]), None)
                if credential:
                    self._retire(credential)
            elif action == "deliver":
                if row["state"] != "approved":
                    raise EnrollmentError("unavailable")
                token = "vx_int_" + secrets.token_urlsafe(32)
                credential = Credential(secrets.token_hex(16), Role.INTEGRATION, row["channel_id"],
                                        verifier(token), enabled=False)
                row.update(state="delivered", credential_id=credential.id)
                self._save(state, (*self.store.records, credential))
                return {**self.public(row), "credential": token, "credential_id": credential.id,
                        "save_required": True}
            self._save(state)
            return self.public(row)
