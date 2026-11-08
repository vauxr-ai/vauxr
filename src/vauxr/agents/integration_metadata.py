"""Public integration configuration provenance from the agent registry."""

from typing import Literal, TypedDict

from vauxr.agents.registry import Agent


class IntegrationMetadata(TypedDict):
    framework: Literal["openclaw", "support", "langgraph"]
    configuration: Literal["enrolled", "operator-managed", "registered", "builtin", "local"]


def public_metadata(agent: Agent) -> IntegrationMetadata:
    """Describe configuration provenance without inferring connection health."""
    return {
        "framework": agent.type if agent.type in ("support", "langgraph") else "openclaw",
        "configuration": (
            "local" if agent.type == "langgraph" else "builtin" if agent.type == "support" else "operator-managed" if agent.type == "openclaw-direct"
            else "enrolled" if agent.integration else "registered"
        ),
    }
