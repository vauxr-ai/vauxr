"""Validated bounded lifecycle namespace; tombstones are never evicted."""

import hashlib
import re

from enrollment_schema import hex_string, timestamp

LIMIT = 1024
TOMBSTONE_LIMIT = 65536
STATES = {"queued", "pending", "delivered", "acknowledged", "completed", "expired", "revoked"}
TERMINAL = {"completed", "expired", "revoked"}


def empty_state() -> dict:
    return {"version": 1, "operations": {}, "blocked": [], "bindings": {}, "recovery": {}}


def validate_lifecycle(state: object) -> None:
    if state == {}:
        return
    try:
        if (not isinstance(state, dict) or state.keys() != empty_state().keys()
                or type(state["version"]) is not int or state["version"] != 1):
            raise ValueError
        blocked = state["blocked"]
        if (not isinstance(blocked, list) or len(blocked) > TOMBSTONE_LIMIT
                or len(set(blocked)) != len(blocked) or not all(hex_string(v, 64) for v in blocked)):
            raise ValueError
        for name in ("operations", "bindings", "recovery"):
            if not isinstance(state[name], dict) or len(state[name]) > LIMIT:
                raise ValueError
        for key, row in state["operations"].items():
            if (not hex_string(key, 32) or row.keys() != {
                "operation_id", "role", "subject", "action", "state", "expires_at", "overlap_until",
                "credential_id", "old_ids", "owner_generation", "origin",
            } or row["operation_id"] != key or row["state"] not in STATES
                    or row["action"] not in ("rotate", "revoke", "recover")
                    or row["role"] not in ("device", "integration")
                    or not isinstance(row["subject"], str) or not 1 <= len(row["subject"]) <= 128
                    or not timestamp(row["expires_at"]) or not timestamp(row["overlap_until"])
                    or not isinstance(row["credential_id"], str)
                    or not isinstance(row["old_ids"], list) or len(row["old_ids"]) > LIMIT
                    or not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", v)
                               for v in row["old_ids"])
                    or not hex_string(row["owner_generation"], 32)
                    or not isinstance(row["origin"], str)):
                raise ValueError
            if (row["overlap_until"] > row["expires_at"]
                    or (row["state"] in ("delivered", "acknowledged", "completed")
                        and not hex_string(row["credential_id"], 32))
                    or (row["action"] == "revoke" and row["state"] != "revoked")):
                raise ValueError
        for subject, row in state["bindings"].items():
            if (not isinstance(subject, str) or row.keys() != {"public_key", "kind"}
                    or not hex_string(row["public_key"], 64) or row["kind"] not in ("physical", "browser")):
                raise ValueError
            if subject != "dev_" + hashlib.sha256(bytes.fromhex(row["public_key"])).hexdigest():
                raise ValueError
        for subject, row in state["recovery"].items():
            if (subject not in state["bindings"] or row.keys() != {"operation_id", "request_id"}
                    or row["operation_id"] not in state["operations"]
                    or not (row["request_id"] == "" or hex_string(row["request_id"], 32))):
                raise ValueError
            operation = state["operations"][row["operation_id"]]
            binding = (operation["subject"], operation["role"], operation["action"])
            if binding != (subject, "device", "recover"):
                raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Invalid lifecycle state") from None
