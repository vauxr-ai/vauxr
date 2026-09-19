import IntegrationEnrollment from "./IntegrationEnrollment";
import { useEffect, useState } from "react";
import { useAgents, type ApiAgent } from "../hooks/useAgents";
export default function AgentsPanel() {
  const api = useAgents();
  const [agents, setAgents] = useState<ApiAgent[]>([]);
  const [error, setError] = useState("");
  const refresh = async () => {
    try {
      setAgents(await api.listAgents());
      setError("");
    } catch {
      setError("Unable to load routing agents.");
    }
  };
  useEffect(() => {
    void refresh();
  }, []);
  return (
    <div className="auth-panel card space-y-4 p-5">
      <h2>Routing agents</h2>
      <p>
        New OpenClaw connections require owner-approved integration enrollment. Manage its
        credentials in Pairing and access.
      </p>
      <IntegrationEnrollment />
      {error && <p role="alert">{error}</p>}
      <button onClick={refresh}>Refresh agents</button>
      {agents.map((c) => (
        <div key={c.id}>
          {c.name} — {c.type} — {c.active ? "Active route" : "Inactive route"}{" "}
          <button
            disabled={c.active}
            onClick={async () => {
              try {
                await api.activateAgent(c.id);
                await refresh();
              } catch {
                setError("Agent activation failed. Refresh before retrying.");
              }
            }}
          >
            Activate
          </button>
        </div>
      ))}
    </div>
  );
}
