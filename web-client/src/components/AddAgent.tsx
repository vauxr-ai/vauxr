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
          onClose={() => setOpen(false)}
          returnFocus={trigger}
        >
          <p>
            Choose an implementation registered on this Vauxr server. Each
            instance keeps its own name, model configuration and voice defaults.
          </p>
          {error && <p role="alert">{error}</p>}
          {!definitions && !error && (
            <p role="status">Loading registered agents…</p>
          )}
          {definitions?.length === 0 && (
            <p>
              No agent definitions registered. Ask the server operator to
              register an implementation.
            </p>
          )}
          <RefreshButton label="Refresh registered agents" busy={busy} onClick={load} />
          {!!definitions?.length && (
            <form
              className="grid gap-3"
              onSubmit={async (e) => {
                e.preventDefault();
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
                } catch {
                  setError(
                    "Agent creation failed or its outcome is uncertain. Refresh agents before retrying.",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                Registered implementation
                <select
                  aria-label="Registered implementation"
                  disabled={busy}
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  {definitions.map((d) => (
                    <option value={d.id} key={d.id}>
                      {d.name} · {d.framework}
                    </option>
                  ))}
                </select>
              </label>
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
              <button
                className="button-primary"
                disabled={busy || !selected || !name.trim()}
              >
                {busy ? "Creating agent…" : "Create agent"}
              </button>
            </form>
          )}
        </Modal>
      )}
    </div>
  );
}
