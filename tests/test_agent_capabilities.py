"""Capability descriptions cannot upgrade routing metadata into authority."""
import json

import pytest

from vauxr.agents.capabilities import public_metadata
from vauxr.agents.registry import Agent
from tests.test_agent_rename import client  # noqa: F401
from tests.auth_helpers import owner_headers


@pytest.mark.parametrize("kind,integration,source", [
    ("openclaw", False, "registered"),
    ("openclaw", True, "enrolled"),
    ("openclaw-direct", False, "operator-managed"),
])
@pytest.mark.parametrize("active", [True, False])
def test_registry_projection_never_implies_negotiation(kind, integration, source, active):
    agent = Agent("private-id", "private-name", kind, active, "private-date", integration=integration)
    # Even unexpected/private peer data must not enter the public projection.
    agent.token = "private-token"
    agent.capabilities = ["ui.agent.name.propose"]
    metadata = public_metadata(agent)
    assert metadata == {
        "framework": "openclaw", "configuration": source,
        "capabilities": [
            {"id": name, "supported": False, "available": False, "reason": "transport_unavailable"}
            for name in ("ui.context.read", "ui.agent.name.propose")
        ],
    }
    assert "private" not in json.dumps(metadata)


async def test_owner_inventory_exposes_only_descriptive_metadata(client):
    assert (await client.get("/api/agents")).status == 401
    response = await client.get("/api/agents", headers=owner_headers(client))
    assert response.status == 200
    row = (await response.json())[0]
    assert row["name"] == "Original" and row["active"] is True
    assert row["integration_metadata"]["configuration"] == "registered"
    assert all(not item["available"] for item in row["integration_metadata"]["capabilities"])
    assert "secret" not in json.dumps(row)
