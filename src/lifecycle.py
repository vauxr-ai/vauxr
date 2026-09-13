"""Transactional subject credential lifecycle. Only verifier digests reach disk."""

import copy
import logging
import secrets
import time
from dataclasses import replace

from auth_policy import Role
from auth_store import Credential, CredentialStore, verifier
from enrollment import TERMINAL as ENROLLMENT_TERMINAL
from enrollment import EnrollmentError, Resolve
from enrollment_schema import hex_string
from lifecycle_schema import LIMIT, TERMINAL, TOMBSTONE_LIMIT, empty_state

log = logging.getLogger("vauxr.lifecycle")

QUEUE_SECONDS = 86400
OVERLAP_SECONDS = 300


class Lifecycle:
    def __init__(self, store: CredentialStore, origin: str) -> None:
        self.store = store
        self.origin = origin

    def _state(self) -> dict:
        return copy.deepcopy(self.store.lifecycle) or empty_state()

    def _save(self, state: dict, records: tuple[Credential, ...], enrollment: dict | None = None) -> None:
        previous = self.store.lifecycle.get("operations", {})
        self.store.lifecycle = state
        if enrollment is not None:
            self.store.enrollment = enrollment
        try:
            self.store.replace(records)
        except BaseException:
            self.store.load()
            raise
        for key, row in state["operations"].items():
            if previous.get(key, {}).get("state") != row["state"]:
                log.info("lifecycle %s %s", row["action"], row["state"])

    def _retire(self, state: dict, ids: set[str], records: tuple[Credential, ...]) -> tuple[Credential, ...]:
        blocked = set(state["blocked"])
        blocked.update(r.verifier for r in records if r.id in ids)
        if len(blocked) > TOMBSTONE_LIMIT:
            raise EnrollmentError("capacity")
        state["blocked"] = sorted(blocked)
        return tuple(replace(r, enabled=False) if r.id in ids else r for r in records)

    def _invalidate_approvals(self, ids: set[str]) -> dict:
        enrollment = copy.deepcopy(self.store.enrollment)
        for row in enrollment.get("requests", {}).values():
            if row["state"] not in ENROLLMENT_TERMINAL and any(
                row[field] and row[field]["credential_id"] in ids for field in ("initiator", "approver")
            ):
                row.update(state="stale", code_hash=row["code_hash"] or "0" * 64)
        return enrollment

    def sweep(self) -> None:
        with self.store.transaction():
            state = self._state()
            records = self.store.records
            changed = False
            retired: set[str] = set()
            for row in state["operations"].values():
                if row["state"] in TERMINAL:
                    continue
                if row["state"] == "acknowledged":
                    row["state"] = "completed"
                    changed = True
                elif (min(row["expires_at"], row["overlap_until"] or row["expires_at"]) <= time.time()
                      or row["owner_generation"] != self.store.owner.get("generation")
                      or row["origin"] != self.origin):
                    ids = {row["credential_id"]}
                    if row["state"] == "delivered":
                        ids.update(row["old_ids"])
                    retired.update(ids)
                    records = self._retire(state, ids, records)
                    row["state"] = "expired"
                    changed = True
            if changed:
                self._save(state, records, self._invalidate_approvals(retired))

    @staticmethod
    def public(row: dict) -> dict:
        return {"version": 1, **{key: row[key] for key in (
            "operation_id", "role", "subject", "action", "state", "expires_at", "overlap_until",
            "credential_id",
        )}}

    def execute(self, action: str, body: dict, resolve: Resolve) -> dict:
        fields = {
            "rotate": {"operation_id", "role", "subject"},
            "revoke": {"operation_id", "role", "subject"},
            "recover": {"operation_id", "role", "subject"},
            "status": {"operation_id"}, "poll": set(), "deliver": {"operation_id"},
            "ack": {"operation_id", "saved"},
        }
        if action not in fields or not isinstance(body, dict) or body.keys() != fields[action]:
            raise EnrollmentError("invalid_request")
        if action != "poll" and not hex_string(body["operation_id"], 32):
            raise EnrollmentError("invalid_request")
        with self.store.transaction():
            self.sweep()
            principal = resolve()
            if principal is None:
                raise EnrollmentError("unauthorized")
            owner = (principal.role == Role.OWNER and principal.subject == "owner"
                     and principal.credential_id == self.store.owner.get("generation")
                     and self.store.owner.get("mode") in ("generated", "environment"))
            if not owner and not self.store.current(principal):
                raise EnrollmentError("unauthorized")
            state = self._state()
            if action in ("rotate", "revoke", "recover"):
                if not owner:
                    raise EnrollmentError("forbidden")
                return self._control(state, action, body)
            if action == "poll":
                if principal.role not in (Role.DEVICE, Role.INTEGRATION):
                    raise EnrollmentError("forbidden")
                row = next((r for r in state["operations"].values()
                            if (r["role"], r["subject"]) == (principal.role, principal.subject)
                            and r["action"] == "rotate" and r["state"] not in TERMINAL), None)
                if row is None:
                    return {"version": 1, "state": "idle"}
                if row["state"] == "queued":
                    row["state"] = "pending"
                    self._save(state, self.store.records)
                return self.public(row)
            row = state["operations"].get(body["operation_id"])
            if row is None:
                raise EnrollmentError("not_found")
            subject = (principal.role, principal.subject) == (row["role"], row["subject"])
            if not subject and not (owner and action == "status"):
                raise EnrollmentError("forbidden")
            if action == "status":
                return self.public(row)
            if action == "ack":
                if body["saved"] is not True or principal.credential_id != row["credential_id"]:
                    raise EnrollmentError("invalid_ack")
                if row["state"] in ("acknowledged", "completed"):
                    return self.public(row)
                if row["state"] != "delivered":
                    raise EnrollmentError("unavailable")
                ids = set(row["old_ids"])
                records = self._retire(state, ids, self.store.records)
                row["state"] = "acknowledged"
                self._save(state, records, self._invalidate_approvals(ids))
                return self.public(row)
            if row["state"] != "pending" or row["action"] != "rotate":
                raise EnrollmentError("unavailable")
            if len(self.store.records) >= LIMIT:
                raise EnrollmentError("capacity")
            token = "vx_" + ("dev_" if principal.role == Role.DEVICE else "int_") + secrets.token_urlsafe(32)
            credential = Credential(secrets.token_hex(16), principal.role, principal.subject, verifier(token))
            row.update(state="delivered", credential_id=credential.id,
                       overlap_until=min(time.time() + OVERLAP_SECONDS, row["expires_at"]))
            self._save(state, (*self.store.records, credential))
            return {**self.public(row), "credential": token, "save_required": True}

    def _control(self, state: dict, action: str, body: dict) -> dict:
        if body["role"] not in ("device", "integration") or not isinstance(body["subject"], str):
            raise EnrollmentError("invalid_request")
        existing = state["operations"].get(body["operation_id"])
        if existing:
            if (existing["action"], existing["role"], existing["subject"]) != (
                action, body["role"], body["subject"]
            ):
                raise EnrollmentError("conflict")
            return self.public(existing)
        records = self.store.records
        matching = [r for r in records if (r.role, r.subject) == (body["role"], body["subject"])]
        if not matching:
            raise EnrollmentError("not_found")
        if len(state["operations"]) >= LIMIT:
            raise EnrollmentError("capacity")
        active = [r for r in state["operations"].values() if r["subject"] == body["subject"]
                  and r["state"] not in TERMINAL]
        if action == "rotate" and (active or not any(self.store.usable(r) for r in matching)):
            raise EnrollmentError("conflict")
        # Integration recovery needs its own participation proof in package #51.
        if action == "recover" and (body["role"] != "device" or body["subject"] not in state["bindings"]):
            raise EnrollmentError("recovery_unavailable")
        row = {**body, "action": action, "state": "queued", "expires_at": time.time() + QUEUE_SECONDS,
               "overlap_until": 0, "credential_id": "", "old_ids": [r.id for r in matching],
               "owner_generation": self.store.owner["generation"], "origin": self.origin}
        enrollment = None
        if action in ("revoke", "recover"):
            ids = {r.id for r in matching}
            records = self._retire(state, ids, records)
            enrollment = self._invalidate_approvals(ids)
            for old in active:
                old["state"] = "revoked"
            state["recovery"].pop(body["subject"], None)
            if action == "revoke":
                row["state"] = "revoked"
            else:
                row["expires_at"] = time.time() + OVERLAP_SECONDS
                state["recovery"][body["subject"]] = {"operation_id": body["operation_id"], "request_id": ""}
            # Invalidate an earlier recovery enrollment even before an actor exists.
            for candidate in enrollment.get("requests", {}).values():
                if (candidate["device_id"] == body["subject"]
                        and candidate["state"] not in ENROLLMENT_TERMINAL):
                    candidate.update(state="stale", code_hash=candidate["code_hash"] or "0" * 64)
        state["operations"][body["operation_id"]] = row
        self._save(state, records, enrollment)
        return self.public(row)

    def bind_enrollment(self, row: dict) -> None:
        """Called under enrollment's transaction; committed with issuance/consumption."""
        state = self._state()
        if row["device_id"] not in state["bindings"] and len(state["bindings"]) >= LIMIT:
            raise EnrollmentError("capacity")
        binding = {"public_key": row["public_key"], "kind": row["kind"]}
        if state["bindings"].get(row["device_id"], binding) != binding:
            raise EnrollmentError("already_owned")
        state["bindings"][row["device_id"]] = binding
        self.store.lifecycle = state
