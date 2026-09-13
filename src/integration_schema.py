"""Bounded integration enrollment metadata; no bearer secrets on disk."""

import re

from enrollment_schema import hex_string, timestamp

LIMIT = 1024
STATES = {"pending", "approved", "delivered", "completed", "denied", "cancelled", "failed",
          "expired", "stale", "revoked"}
TERMINAL = STATES - {"pending", "approved", "delivered"}
PUBLIC = ("request_id", "server_id", "origin", "channel_id", "display_name", "expires_at", "state")


def display_name(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()-]{0,63}", value) is not None


def empty_state() -> dict:
    return {"version": 1, "requests": {}, "active_channel": ""}


def validate_integration(state: object) -> None:
    if state == {}:
        return
    try:
        if (not isinstance(state, dict) or state.keys() != empty_state().keys()
                or type(state["version"]) is not int or state["version"] != 1
                or not isinstance(state["requests"], dict) or len(state["requests"]) > LIMIT):
            raise ValueError
        for key, row in state["requests"].items():
            if (not isinstance(row, dict) or row.keys() != set(PUBLIC) | {
                "secret_hash", "code_hash", "owner_generation", "attempts", "credential_id", "created_at"}
                    or not hex_string(key, 32) or row["request_id"] != key
                    or row["channel_id"] != "int_" + key or row["state"] not in STATES
                    or not hex_string(row["server_id"], 32) or not isinstance(row["origin"], str)
                    or not display_name(row["display_name"])
                    or not all(hex_string(row[field], 64) for field in ("secret_hash", "code_hash"))
                    or not hex_string(row["owner_generation"], 32)
                    or type(row["attempts"]) is not int or not 0 <= row["attempts"] <= 5
                    or not timestamp(row["expires_at"]) or not timestamp(row["created_at"])
                    or not (row["credential_id"] == "" or hex_string(row["credential_id"], 32))
                    or (row["state"] in ("delivered", "completed") and not row["credential_id"])):
                raise ValueError
        active = state["active_channel"]
        if not isinstance(active, str) or (active and not any(
            r["channel_id"] == active and r["credential_id"] for r in state["requests"].values()
        )):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("Invalid integration state") from None
