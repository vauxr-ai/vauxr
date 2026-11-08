import type { IntegrationMetadata as Metadata } from "../hooks/useAgents";

const sources = {
  local: "Local configured integration",
  builtin: "Built in",
  enrolled: "Enrolled connection",
  "operator-managed": "Operator-managed connection",
  registered: "Registered route",
};

export default function IntegrationMetadata({ metadata }: { metadata?: Metadata }) {
  return <section aria-label="Integration configuration" className="space-y-3">
    <h3>Integration configuration</h3>
    {metadata ? <p>{metadata.framework === "support" ? "Vauxr Support" : metadata.framework === "langchain" ? "LangChain" : metadata.framework === "langgraph" ? "Standalone agent" : "OpenClaw"} · {sources[metadata.configuration]}</p>
      : <p>Integration configuration source is unavailable.</p>}
    <p>Configuration source does not prove connectivity. Check device diagnostics for connection status and the agent’s voice pipeline for voice configuration.</p>
  </section>;
}
