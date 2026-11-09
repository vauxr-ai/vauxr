import LoadingIndicator from "./LoadingIndicator";
import IntegrationEnrollment from "./IntegrationEnrollment";
import RefreshButton from "./RefreshButton";
import { useEffect, useState, useRef } from "react";
import Modal from "./Modal";
import { ownerFetch, jsonResponse } from "../auth/api";
type Definition = {
  id: string;
  name: string;
  framework: string;
  supported_modes: string[];
};
export default function AddAgent({
  onCreated,
}: {
  onCreated: (agent: { id: string; name: string }) => Promise<void>;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [definitions, setDefinitions] = useState<Definition[] | null>(null);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setError("");
    try {
      const result = await jsonResponse(await ownerFetch("/api/local-agents"));
      if (!Array.isArray(result.definitions)) throw new Error();
      setDefinitions(result.definitions);
      setSelected(result.definitions[0]?.id || "");
    } catch {
      setError(
        "Registered agent definitions unavailable. Refresh to retry. External agents can be connected in Settings → Apps & Integrations.",
      );
    }
  }
  useEffect(() => {
    void load();
  }, []);
  return (
    <div className="agent-create-action">
      <button
        ref={trigger}
        className="button-primary"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        + Add Agent
      </button>
      {open && (
        <Modal
          title="Add Agent"
          className="agent-create-modal"
          onClose={() => {if (!busy) setOpen(false);}}
          returnFocus={trigger}
        >
          <p>
            Choose your agent type. Each agent keeps its own identity and voice defaults.
          </p>
          <a className="agent-guide-link" href="https://github.com/vauxr-ai/vauxr/blob/docs/maintainer-review-20261109/docs/how-to-create-custom-agent.md"
            target="_blank" rel="noopener noreferrer">
            How to create a custom agent <span className="sr-only">(opens in a new tab)</span><span aria-hidden="true">↗</span>
          </a>
          {error && <p role="alert">{error}</p>}
          {!definitions && !error && (
            <LoadingIndicator label="Loading registered agents"/>
          )}
          {definitions?.length === 0 && (
            <p>
              No agent definitions registered. Ask the server operator to
              register an implementation.
            </p>
          )}
          <RefreshButton label="Refresh registered agents" busy={busy} onClick={load} />
          <div className={`agent-type-choices ${selected === "@openclaw" ? "is-connecting" : ""}`} role="group" aria-label="Agent type">
            <button type="button" className="agent-type-choice" aria-label="Choose OpenClaw" aria-pressed={selected === "@openclaw"} disabled={busy} onClick={() => setSelected("@openclaw")}>
              <span className="agent-type-art"><img src="/agent-types/openclaw.svg" alt=""/></span><strong>OpenClaw</strong><small>Connect your integration</small>
            </button>
            {definitions?.map(d => <button type="button" key={d.id} className="agent-type-choice" aria-label={`Choose ${d.name}`} aria-pressed={selected === d.id} disabled={busy} onClick={() => setSelected(d.id)}>
              <span className="agent-type-art">{d.id === "vauxr-support" ? <img src="/agent-types/vauxr-support.png" alt=""/> : <svg aria-hidden="true" viewBox="0 0 64 64" fill="none" stroke="currentColor" strokeWidth="2"><path d="m32 5 24 14v26L32 59 8 45V19ZM8 19l24 14 24-14M32 33v26"/></svg>}</span>
              <strong>{d.name}</strong><small>{d.supported_modes.includes("standard") ? "Voice & text" : "Text agent"}</small>
            </button>)}
          </div>
          {selected === "@openclaw" ? <IntegrationEnrollment compact/> : !!definitions?.length && (
            <form
              className="grid gap-3" aria-busy={busy}
              onSubmit={async (e) => {
                e.preventDefault();
                if (busy) return;
                setBusy(true);
                setError("");
                try {
                  const created = await jsonResponse(
                    await ownerFetch("/api/local-agents", {
                      method: "POST",
                      body: JSON.stringify({
                        definition_id: selected,
                        name: name.trim(),
                      }),
                    }),
                  );
                  await onCreated(created);
                  setName("");
                  setOpen(false);
                } catch (err) {
                  setError(err instanceof Error ? err.message : "Agent creation failed. Refresh agents before retrying.");
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                Agent name
                <input
                  required
                  maxLength={120}
                  disabled={busy}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              {busy && <p role="status">Creating agent…</p>}
              <div className="modal-actions"><button
                className="button-primary"
                disabled={busy || !selected || !name.trim()}
              >
                {busy ? "Creating agent…" : "Create agent"}
              </button></div>
            </form>
          )}
        </Modal>
      )}
    </div>
  );
}
