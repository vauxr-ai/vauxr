"""Strict persisted enrollment v1 schema, shared with the credential snapshot."""

import hashlib
import math
import re

MAX_REQUESTS = 64
TTL = 300
STATES = {"challenge", "ready", "initiated", "approved", "consumed", "denied", "cancelled", "failed", "stale"}
BINDING = (
    "version",
    "request_id",
    "server_id",
    "origin",
    "kind",
    "public_key",
    "device_id",
    "nonce",
    "expires_at",
    "owner_generation",
    "display_name",
)


def hex_string(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None


def timestamp(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and (value >= 0)


def validate_enrollment(state: object) -> None:
    if state == {}:
        return
    try:
        if not (isinstance(state, dict) and state.keys() == {"version", "server_id", "requests", "attempts"}):
            raise ValueError("Invalid enrollment state")
        if not (type(state["version"]) is int and state["version"] == 1):
            raise ValueError("Invalid enrollment state")
        if not hex_string(state["server_id"], 32):
            raise ValueError("Invalid enrollment state")
        if not (isinstance(state["attempts"], list) and len(state["attempts"]) <= 60):
            raise ValueError("Invalid enrollment state")
        if not all(timestamp(at) for at in state["attempts"]):
            raise ValueError("Invalid enrollment state")
        if not (isinstance(state["requests"], dict) and len(state["requests"]) <= MAX_REQUESTS):
            raise ValueError("Invalid enrollment state")
        for key, row in state["requests"].items():
            if not isinstance(row, dict):
                raise TypeError("Invalid enrollment state")
            if not row.keys() == set(BINDING) | {"state", "attempts", "code_hash", "initiator", "approver"}:
                raise ValueError("Invalid enrollment state")
            if not (key == row["request_id"] and hex_string(key, 32)):
                raise ValueError("Invalid enrollment state")
            if not (type(row["version"]) is int and row["version"] == 1):
                raise ValueError("Invalid enrollment state")
            if not row["server_id"] == state["server_id"]:
                raise ValueError("Invalid enrollment state")
            if not (isinstance(row["origin"], str) and row["origin"].startswith("https://")):
                raise ValueError("Invalid enrollment state")
            if not (len(row["origin"]) <= 256 and row["origin"].isascii()):
                raise ValueError("Invalid enrollment state")
            if not row["kind"] in ("physical", "browser"):
                raise ValueError("Invalid enrollment state")
            if not (hex_string(row["public_key"], 64) and hex_string(row["nonce"], 64)):
                raise ValueError("Invalid enrollment state")
            if not (isinstance(row["device_id"], str) and re.fullmatch("dev_[0-9a-f]{64}", row["device_id"])):
                raise ValueError("Invalid enrollment state")
            if row["device_id"] != "dev_" + hashlib.sha256(bytes.fromhex(row["public_key"])).hexdigest():
                raise ValueError("Invalid enrollment state")
            if not (timestamp(row["expires_at"]) and type(row["expires_at"]) is int):
                raise ValueError("Invalid enrollment state")
            if not hex_string(row["owner_generation"], 32):
                raise ValueError("Invalid enrollment state")
            if not (
                isinstance(row["display_name"], str)
                and re.fullmatch("[A-Za-z0-9 _.-]{1,64}", row["display_name"])
            ):
                raise ValueError("Invalid enrollment state")
            if not row["state"] in STATES:
                raise ValueError("Invalid enrollment state")
            if not (type(row["attempts"]) is int and 0 <= row["attempts"] <= 5):
                raise ValueError("Invalid enrollment state")
            if not (row["code_hash"] == "" or hex_string(row["code_hash"], 64)):
                raise ValueError("Invalid enrollment state")
            if not (row["state"] == "challenge" or hex_string(row["code_hash"], 64)):
                raise ValueError("Invalid enrollment state")
            for field in ("initiator", "approver"):
                actor = row[field]
                if actor is None:
                    continue
                if not isinstance(actor, dict):
                    raise TypeError("Invalid enrollment state")
                if not actor.keys() == {"role", "subject", "credential_id", "credential_generation"}:
                    raise ValueError("Invalid enrollment state")
                if not actor["role"] in ("owner", "integration"):
                    raise ValueError("Invalid enrollment state")
                if not all(isinstance(value, str) and len(value) <= 128 for value in actor.values()):
                    raise ValueError("Invalid enrollment state")
                if row["kind"] == "browser" and actor["role"] != "owner":
                    raise ValueError("Invalid enrollment state")
                if actor["role"] == "owner":
                    if not (actor["subject"] == "owner" and actor["credential_generation"] == ""):
                        raise ValueError("Invalid enrollment state")
                    if not hex_string(actor["credential_id"], 32):
                        raise ValueError("Invalid enrollment state")
                elif not hex_string(actor["credential_generation"], 64):
                    raise ValueError("Invalid enrollment state")
            if row["state"] in ("initiated", "approved", "consumed") and row["initiator"] is None:
                raise ValueError("Invalid enrollment state")
            if row["state"] in ("approved", "consumed") and row["approver"] is None:
                raise ValueError("Invalid enrollment state")
    except (KeyError, TypeError, ValueError):
        raise ValueError("Invalid enrollment state") from None
