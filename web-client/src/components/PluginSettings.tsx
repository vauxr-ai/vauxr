import RefreshButton from "./RefreshButton";
import { useEffect, useState } from "react";
import { settingsRequest } from "./ProviderSettings";
type Field = {
  key: string;
  label: string;
  type: "string" | "boolean" | "integer" | "secret";
  required: boolean;
  default?: unknown;
  choices: string[];
  min?: number;
  max?: number;
};
type Plugin = {
  id: string;
  name: string;
  fields: Field[];
  values: Record<string, string | number | boolean>;
  secrets: Record<string, boolean>;
};
export default function PluginSettings({
  onDirtyChange,
}: {
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [plugins, setPlugins] = useState<Plugin[] | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [dirtyIds, setDirtyIds] = useState<string[]>([]);
  const dirty = dirtyIds.length > 0;
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
  async function load() {
    try {
      setPlugins((await settingsRequest("/api/settings/plugins")).plugins);
      setError("");
      setDirtyIds([]);
    } catch {
      setError("Plugin settings unavailable. Refresh to retry.");
    }
  }
  useEffect(() => {
    void load();
  }, []);
  return (
    <section className="settings-stack" aria-label="Plugin settings">
      <div className="settings-intro">
        <span className="eyebrow">Extensions</span>
        <h2>Plugin settings</h2>
        <p>
          Only settings declared by plugins loaded in this Vauxr server appear
          here. Package downloads and external connection setup are separate.
        </p>
      </div>
      <RefreshButton label="Refresh plugin settings" busy={busy} onClick={async () => {if (!dirty || confirm("Discard unsaved plugin edits?")) await load();}} />
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!plugins && !error && <p role="status">Loading declared settings…</p>}
      {plugins?.length === 0 && (
        <div className="settings-empty">
          <h3>No plugin settings registered</h3>
          <p>
            The installed plugins have not declared configurable server
            settings. Available integration packages are listed below.
          </p>
        </div>
      )}
      {plugins?.map((p) => (
        <form
          key={p.id + JSON.stringify(p.values) + JSON.stringify(p.secrets)}
          className="card settings-form"
          onChange={() =>
            setDirtyIds((ids) => (ids.includes(p.id) ? ids : [...ids, p.id]))
          }
          onSubmit={async (e) => {
            e.preventDefault();
            const form = e.currentTarget;
            const fields = new FormData(form);
            const values: Record<string, unknown> = {};
            const secrets: Record<string, string> = {};
            const clear_secrets: string[] = [];
            for (const f of p.fields) {
              const value = fields.get(f.key);
              if (f.type === "secret") {
                if (value) secrets[f.key] = String(value);
                if (fields.get(`clear:${f.key}`)) clear_secrets.push(f.key);
                const input = form.elements.namedItem(
                  f.key,
                ) as HTMLInputElement;
                if (input) input.value = "";
              } else
                values[f.key] =
                  f.type === "boolean"
                    ? value === "on"
                    : f.type === "integer"
                      ? Number(value)
                      : String(value ?? "");
            }
            setBusy(true);
            setError("");
            try {
              setPlugins(
                (
                  await settingsRequest(
                    `/api/settings/plugins/${encodeURIComponent(p.id)}`,
                    { values, secrets, clear_secrets },
                  )
                ).plugins,
              );
              setDirtyIds((ids) => ids.filter((id) => id !== p.id));
              setStatus("Plugin settings saved.");
            } catch {
              setError(
                "Plugin settings could not be saved. Check required fields and refresh before retrying. Re-enter a secret only to replace it.",
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <h3>{p.name}</h3>
          <fieldset disabled={busy} className="settings-fields">
            {p.fields.map((f) => (
              <div key={f.key}>
                {f.type === "boolean" ? (
                  <label className="check-field">
                    <input
                      type="checkbox"
                      name={f.key}
                      defaultChecked={p.values[f.key] === true}
                    />
                    {f.label}
                  </label>
                ) : (
                  <label>
                    {f.label}
                    {f.choices.length ? (
                      <select
                        name={f.key}
                        defaultValue={String(p.values[f.key] ?? "")}
                      >
                        {f.choices.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    ) : (
                      <input
                        name={f.key}
                        type={
                          f.type === "secret"
                            ? "password"
                            : f.type === "integer"
                              ? "number"
                              : "text"
                        }
                        autoComplete={
                          f.type === "secret" ? "new-password" : undefined
                        }
                        maxLength={4096}
                        min={f.min ?? undefined}
                        max={f.max ?? undefined}
                        required={
                          f.required &&
                          !(f.type === "secret" && p.secrets[f.key])
                        }
                        defaultValue={
                          f.type === "secret"
                            ? ""
                            : String(p.values[f.key] ?? "")
                        }
                      />
                    )}
                  </label>
                )}
                {f.type === "secret" && (
                  <>
                    <small>
                      {p.secrets[f.key]
                        ? "Credential configured. Leave blank to retain."
                        : "Credential not configured."}
                    </small>
                    <label className="check-field">
                      <input type="checkbox" name={`clear:${f.key}`} />
                      Clear saved {f.label}
                    </label>
                  </>
                )}
              </div>
            ))}
          </fieldset>
          <button disabled={busy}>
            {busy ? "Saving…" : "Save plugin settings"}
          </button>
        </form>
      ))}
      {dirty && <p role="status">Unsaved plugin edits</p>}
    </section>
  );
}
