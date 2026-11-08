"""Public integration configuration provenance from the agent registry."""

from typing import Literal, TypedDict

from vauxr.agents.registry import Agent


class IntegrationMetadata(TypedDict):
    framework: Literal["openclaw", "support"]
    configuration: Literal["enrolled", "operator-managed", "registered", "builtin"]


def public_metadata(agent: Agent) -> IntegrationMetadata:
    """Describe configuration provenance without inferring connection health."""
    return {
        "framework": "support" if agent.type == "support" else "openclaw",
        "configuration": (
            "builtin" if agent.type == "support" else "operator-managed" if agent.type == "openclaw-direct"
            else "enrolled" if agent.integration else "registered"
        ),
    }
