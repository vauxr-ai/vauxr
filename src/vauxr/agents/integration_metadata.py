"""Public integration configuration provenance from the agent registry."""

from typing import Literal, TypedDict

from vauxr.agents.registry import Agent


class IntegrationMetadata(TypedDict):
    framework: Literal["openclaw"]
    configuration: Literal["enrolled", "operator-managed", "registered"]


def public_metadata(agent: Agent) -> IntegrationMetadata:
    """Describe configuration provenance without inferring connection health."""
    return {
        "framework": "openclaw",
        "configuration": (
            "operator-managed" if agent.type == "openclaw-direct"
            else "enrolled" if agent.integration else "registered"
        ),
    }
