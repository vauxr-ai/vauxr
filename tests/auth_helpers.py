"""Explicit schema fixtures; never infer enrollment from DEVICE_TOKEN."""

from auth import get_store
from auth_policy import Role
from auth_store import Credential, verifier


def seed(token: str, role: Role, subject: str) -> None:
    store = get_store()
    record = Credential(subject + "-credential", role, subject, verifier(token))
    store.replace(tuple(r for r in store.records if r.id != record.id) + (record,))


def owner_headers(client) -> dict[str, str]:
    """A real saved-token session over the synthetic trusted proxy boundary."""
    from owner_http import COOKIE, OWNER
    service = client.app[OWNER]
    if service.status()["state"] != "generated":
        result = service.claim(service.console_claim(recover=True))
        service.acknowledge(result["save_acknowledgement"], True)
        client._owner_test_token = result["operator_token"]
    cookie, session = service.login(client._owner_test_token)
    return {"Host": "owner.example", "Origin": "https://owner.example",
            "X-Forwarded-Proto": "https", "Cookie": f"{COOKIE}={cookie}", "X-CSRF-Token": session.csrf}
