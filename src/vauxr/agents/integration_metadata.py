"""Public integration configuration provenance from the agent registry."""

from typing import Literal, TypedDict

from vauxr.agents.registry import Agent


class IntegrationMetadata(TypedDict):
    framework: Literal["openclaw", "langchain"]
    configuration: Literal["enrolled", "operator-managed", "registered", "builtin", "local"]


def public_metadata(agent: Agent) -> IntegrationMetadata:
    """Describe configuration provenance without inferring connection health."""
    return {
        "framework": agent.type if agent.type == "langchain" else "openclaw",
        "configuration": (
            "builtin" if agent.builtin else "local" if agent.type == "langchain" else "operator-managed" if agent.type == "openclaw-direct"
            else "enrolled" if agent.integration else "registered"
        ),
    }
