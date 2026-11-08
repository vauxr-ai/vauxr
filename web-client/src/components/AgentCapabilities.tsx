import type { IntegrationMetadata } from "../hooks/useAgents";

const labels = {
  "ui.context.read": "Read UI context",
  "ui.agent.name.propose": "Propose an agent name change",
};

export default function AgentCapabilities({ metadata }: { metadata?: IntegrationMetadata }) {
  return <section aria-label="Integration capabilities" className="space-y-3">
    <h3>Integration capabilities</h3>
    {metadata ? <>
      <p>OpenClaw · {metadata.configuration === "enrolled" ? "Enrolled connection" : metadata.configuration === "operator-managed" ? "Operator-managed connection" : "Registered route"}</p>
      <p>Configuration source does not prove connectivity. Voice modes and defaults are configured separately below.</p>
      <ul>{metadata.capabilities.map(capability => <li key={capability.id}>
        <strong>{labels[capability.id]}</strong>: {capability.supported && capability.available ? "Available" : "Unavailable"}
        {capability.reason === "transport_unavailable" && " — UI capability transport is not implemented for this route."}
      </li>)}</ul>
    </> : <p>Capability metadata is unavailable. No UI capabilities are assumed.</p>}
    <p>Capabilities describe integration support, not permission. A connection or active route grants no additional administration access. Proposing a name change is not permission to save it.</p>
    <p>Plugin installation and model-provider configuration are not available here.</p>
  </section>;
}
