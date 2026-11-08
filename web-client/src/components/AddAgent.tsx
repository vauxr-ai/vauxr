import { useEffect, useState } from "react";
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
    <section className="card auth-panel p-5 space-y-3" aria-label="Add Agent">
      <button aria-expanded={open} onClick={() => setOpen(!open)}>
        + Add Agent
      </button>
      {open && (
        <>
          <h2>Add Agent</h2>
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
          <button disabled={busy} onClick={() => void load()}>
            Refresh registered agents
          </button>
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
              <button disabled={busy || !selected || !name.trim()}>
                Create agent
              </button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
