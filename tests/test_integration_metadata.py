"""Integration metadata describes registry provenance only."""
import json

import pytest

from vauxr.agents.integration_metadata import public_metadata
from vauxr.agents.registry import Agent
from tests.test_agent_rename import client  # noqa: F401
from tests.auth_helpers import owner_headers


@pytest.mark.parametrize("kind,integration,source", [
    ("openclaw", False, "registered"),
    ("openclaw", True, "enrolled"),
    ("openclaw-direct", False, "operator-managed"),
])
@pytest.mark.parametrize("active", [True, False])
def test_registry_projection_contains_only_provenance(kind, integration, source, active):
    agent = Agent("private-id", "private-name", kind, active, "private-date", integration=integration)
    # Even unexpected/private peer data must not enter the public projection.
    agent.token = "private-token"
    agent.peer_metadata = {"unexpected": "private-value"}
    metadata = public_metadata(agent)
    assert metadata == {
        "framework": "openclaw", "configuration": source,
    }
    assert "private" not in json.dumps(metadata)


async def test_owner_inventory_exposes_only_descriptive_metadata(client):
    assert (await client.get("/api/agents")).status == 401
    response = await client.get("/api/agents", headers=owner_headers(client))
    assert response.status == 200
    row = (await response.json())[0]
    assert row["name"] == "Original" and row["active"] is True
    assert row["integration_metadata"] == {"framework": "openclaw", "configuration": "registered"}
    assert "secret" not in json.dumps(row)
