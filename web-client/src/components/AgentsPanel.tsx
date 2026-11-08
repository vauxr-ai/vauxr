import SupportSettings from "./SupportSettings";
import IntegrationMetadata from "./IntegrationMetadata";
import IntegrationEnrollment from "./IntegrationEnrollment";
import { useEffect, useState } from "react";
import { useAgents, type ApiAgent } from "../hooks/useAgents";
import Icon from "./Icon";
import SpeechSettings from "./SpeechSettings";

export default function AgentsPanel({
  onContext,
  onSelection,
}: {
  onContext?: (context: string) => void;
  onSelection?: (selection: { id: string; name: string } | null) => void;
}) {
  const api = useAgents();
  const [agents, setAgents] = useState<ApiAgent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [saved, setSaved] = useState("");
  const selected = agents.find((agent) => agent.id === selectedId);
  useEffect(() => {
    onContext?.(selected?.name || "Agents");
  }, [selected?.name, onContext]);
  useEffect(() => {
    onSelection?.(selected ? { id: selected.id, name: selected.name } : null);
  }, [selected?.id, selected?.name, onSelection]);
  const refresh = async () => {
    try {
      setAgents(await api.listAgents());
      setError("");
    } catch {
      setError("Unable to load routing agents. Try refreshing.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void refresh();
    const update = () => { void refresh(); };
    window.addEventListener("companion-resource-changed", update);
    const policyUpdate = (event: Event) => { if ((event as CustomEvent).detail?.agentId) update(); };
    window.addEventListener("speech-settings-changed", policyUpdate);
    return () => {
      window.removeEventListener("companion-resource-changed", update);
      window.removeEventListener("speech-settings-changed", policyUpdate);
    };
  }, []);

  async function activate(agent: ApiAgent) {
    if (
      !window.confirm(
        `Make ${agent.name} the active fallback route for unassigned devices? This changes server routing, not just your companion.`,
      )
    )
      return;
    setBusy(true);
    try {
      await api.activateAgent(agent.id);
      await refresh();
      window.dispatchEvent(new Event("agent-route-changed"));
    } catch (error) {
      setError(error instanceof Error ? error.message : "Agent activation failed. Refresh before retrying.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="card agent-inventory" aria-label="Routing agents">
        <div className="section-heading">
          <div>
            <h2>Routing agents</h2>
            <p>
              The active route serves devices that have no explicit agent
              assignment.
            </p>
          </div>
          <button disabled={loading || busy} onClick={refresh}>
            Refresh agents
          </button>
        </div>
        {error && (
          <p role="alert" className="p-5 text-red-300">
            {error}
          </p>
        )}
        {loading ? (
          <p role="status" className="p-5">
            Loading agents…
          </p>
        ) : agents.length === 0 ? (
          <div className="empty-state">
            <Icon name="agents" size={32} />
            <h3>No routing agents yet</h3>
            <p>Approve an OpenClaw connection below to get started.</p>
          </div>
        ) : (
          <ul className="agent-list">
            {agents.map((agent) => (
              <li key={agent.id}>
                <span className="resource-icon">
                  <Icon name="agents" size={22} />
                </span>
                <div className="agent-name">
                  <h3>
                    <button
                      aria-label={`View agent ${agent.name}`}
                      onClick={() => {
                        setSelectedId(agent.id);
                        setNameDraft(agent.name);
                        setSaved("");
                      }}
                    >
                      {agent.name}
                    </button>
                  </h3>
                  <p>
                    {agent.type === "support" ? "Built-in · independent runtime" : agent.type === "openclaw-direct"
                      ? "OpenClaw direct"
                      : "OpenClaw connection"}
                  </p>
                </div>
                <span className="pill">
                  {agent.chat_only ? "Chat-only · no voice" : agent.active ? "Active route" : "Inactive route"}
                </span>
                <button
                  disabled={agent.active || busy}
                  onClick={() => void activate(agent)}
                >
                  {agent.active ? "Active" : "Activate"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {selectedId && (
        <section
          className="card auth-panel agent-detail p-5"
          aria-label="Agent detail"
        >
          {selected ? (
            <>
              <h2>{selected.name}</h2>
              <p>
                {selected.type === "support" ? "Built-in · independent runtime" : selected.type === "openclaw-direct"
                  ? "OpenClaw direct"
                  : "OpenClaw connection"}{" "}
                · {selected.active ? "Active route" : "Inactive route"}
              </p>
              <p>
                This route is shared by devices. Changing its display name does
                not change routing or access.
              </p>
              <p>
                Agent ID: <code>{selected.id}</code>
              </p>
              <p>{selected.active ? "Fallback for unassigned devices only. Explicit device assignments and your independent companion selection are unchanged." : "Inactive as the shared fallback. Explicit device assignments remain separate."} Route activation does not prove the integration is online or ready; check effective voice configuration below and device diagnostics.</p>
              <IntegrationMetadata metadata={selected.integration_metadata} />
              {selected.type === "support" && <SupportSettings />}
              <SpeechSettings key={selected.id} agentId={selected.id} />
              <form
                onSubmit={async (event) => {
                  event.preventDefault();
                  setBusy(true);
                  setError("");
                  setSaved("");
                  try {
                    const renamed = await api.renameAgent(
                      selected.id,
                      nameDraft.trim(),
                    );
                    setAgents((current) =>
                      current.map((agent) =>
                        agent.id === renamed.id
                          ? { ...agent, name: renamed.name }
                          : agent,
                      ),
                    );
                    setNameDraft(renamed.name);
                    setSaved("Agent name saved.");
                    window.dispatchEvent(new Event("agent-route-changed"));
                  } catch (error) {
                    setError(
                      error instanceof Error
                        ? error.message
                        : "Unable to save the agent name.",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <label>
                  Agent name
                  <input
                    data-vauxr-control="agent-name"
                    value={nameDraft}
                    maxLength={120}
                    required
                    disabled={busy}
                    onChange={(event) => {
                      setNameDraft(event.target.value);
                      setSaved("");
                    }}
                  />
                </label>
                <button
                  disabled={
                    busy ||
                    !nameDraft.trim() ||
                    nameDraft.trim() === selected.name
                  }
                >
                  Save name
                </button>
                {nameDraft.trim() !== selected.name && <p>Unsaved name</p>}
                {saved && <p role="status">{saved}</p>}
              </form>
            </>
          ) : (
            <p role="status">
              This agent is no longer available. Refresh the agent list.
            </p>
          )}
          <button disabled={busy} onClick={() => setSelectedId("")}>
            Close agent detail
          </button>
        </section>
      )}
      <section className="card auth-panel enrollment-section">
        <h2>Connect an agent</h2>
        <p>OpenClaw enrollment is the supported connection workflow. Start enrollment in your OpenClaw Vauxr integration; this page approves its request rather than creating an agent or installing a plugin.</p>
        <p>OpenClaw Direct, when configured by the server operator, is an existing alternative route. No connection, credential or plugin editor is available here.</p>
        <p>
          Approve an integration request from OpenClaw. Credentials are managed
          in Settings → Pairing and access.
        </p>
        <IntegrationEnrollment />
      </section>
    </div>
  );
}
