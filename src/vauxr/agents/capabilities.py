"""Public, descriptive integration metadata. Never an authorization input."""

from typing import Literal, TypedDict

from vauxr.agents.registry import Agent

CapabilityId = Literal["ui.context.read", "ui.agent.name.propose"]


class Capability(TypedDict):
    id: CapabilityId
    supported: bool
    available: bool
    reason: Literal["transport_unavailable"]


class IntegrationMetadata(TypedDict):
    framework: Literal["openclaw"]
    configuration: Literal["enrolled", "operator-managed", "registered"]
    capabilities: list[Capability]


def public_metadata(agent: Agent) -> IntegrationMetadata:
    """Project only registry provenance and audited end-to-end support.

    The existing OpenClaw transport has no UI context/proposal negotiation.
    Registration, active routing and peer advertisements cannot grant these
    capabilities. Connectivity and voice policy are separate, existing views.
    """
    return {
        "framework": "openclaw",
        "configuration": (
            "operator-managed" if agent.type == "openclaw-direct"
            else "enrolled" if agent.integration else "registered"
        ),
        "capabilities": [
            {"id": capability, "supported": False, "available": False,
             "reason": "transport_unavailable"}
            for capability in ("ui.context.read", "ui.agent.name.propose")
        ],
    }
