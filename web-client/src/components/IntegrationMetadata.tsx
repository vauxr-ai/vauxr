import type { IntegrationMetadata as Metadata } from "../hooks/useAgents";

const sources = {
  enrolled: "Enrolled connection",
  "operator-managed": "Operator-managed connection",
  registered: "Registered route",
};

export default function IntegrationMetadata({ metadata }: { metadata?: Metadata }) {
  return <section aria-label="Integration configuration" className="space-y-3">
    <h3>Integration configuration</h3>
    {metadata ? <p>OpenClaw · {sources[metadata.configuration]}</p>
      : <p>Integration configuration source is unavailable.</p>}
    <p>Configuration source does not prove connectivity. Check device diagnostics for connection status and the voice settings below for voice configuration.</p>
  </section>;
}
