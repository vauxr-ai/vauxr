import { useEffect, useRef, useState } from "react";
import { jsonResponse, ownerFetch } from "../auth/api";

export type Profile = {
  id: string;
  name: string;
  provider: "ollama" | "openai";
  endpoint: string;
  model: string;
  auth: "none" | "environment" | "inline";
  credential_configured: boolean;
};
type Profiles = {
  profiles: Profile[];
  speech_auth: Record<string, string>;
  capabilities: { openai_environment_configured: boolean };
};
type Backend = {
  id: string;
  kind: "stt" | "tts" | "realtime";
  adapter: string;
  model: string;
  host: string;
  port: number;
  voices: string[];
};
export async function settingsRequest(
  path: string,
  body?: object,
  method = "PUT",
) {
  return jsonResponse(
    await ownerFetch(path, body ? { method, body: JSON.stringify(body) } : {}),
  );
}
const blank: Profile = {
  id: "",
  name: "",
  provider: "ollama",
  endpoint: "http://localhost:11434",
  model: "",
  auth: "none",
  credential_configured: false,
};

export function ModelProviders({
  onDirtyChange,
}: {
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [data, setData] = useState<Profiles | null>(null);
  const [draft, setDraft] = useState<Profile | null>(null);
  const [original, setOriginal] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [secretDirty, setSecretDirty] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const form = useRef<HTMLFormElement>(null);
  const dirty = !!draft && (JSON.stringify(draft) !== original || secretDirty);
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
  async function load() {
    setError("");
    try {
      setData(await settingsRequest("/api/settings/providers"));
    } catch {
      setError("Provider profiles unavailable. Refresh to retry.");
    }
  }
  useEffect(() => {
    void load();
  }, []);
  function edit(p: Profile) {
    if (dirty && !confirm("Discard unsaved provider edits?")) return;
    setDraft({ ...p });
    setOriginal(JSON.stringify(p));
    setSecretDirty(false);
    setModels([]);
    setStatus("");
    setError("");
    form.current?.reset();
  }
  return (
    <div className="settings-stack">
      <div className="settings-intro">
        <span className="eyebrow">Connections & authentication</span>
        <h2>Model providers</h2>
        <p>
          Save a model connection once. Agent runtimes choose their own provider
          configuration in Agents. OpenAI profiles can also supply credentials
          to Standard TTS without sharing voice or model selections.
        </p>
      </div>
      <div className="settings-toolbar">
        <button disabled={busy} onClick={() => edit(blank)}>
          + Add provider profile
        </button>
        <button disabled={busy} onClick={() => void load()}>
          Refresh profiles
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!data && !error && <p role="status">Loading provider profiles…</p>}
      {data && data.profiles.length === 0 && (
        <div className="settings-empty">
          <h3>No saved profiles</h3>
          <p>
            Add a connection to a separately installed Ollama service or an
            OpenAI account. Existing environment-backed speech providers
            continue to work.
          </p>
        </div>
      )}
      <div className="provider-grid">
        {data?.profiles.map((p) => (
          <button
            className="provider-tile"
            key={p.id}
            onClick={() => edit(p)}
            aria-pressed={draft?.id === p.id}
          >
            <span className="eyebrow">{p.provider}</span>
            <strong>{p.name}</strong>
            <span>{p.model}</span>
            <span className="pill">
              {p.auth === "none"
                ? "No credential required"
                : p.credential_configured
                  ? "Credential configured"
                  : "Credential missing"}
            </span>
          </button>
        ))}
      </div>
      {draft && (
        <form
          ref={form}
          className="card settings-form"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            const fields = new FormData(event.currentTarget);
            const credential = String(fields.get("credential") || "");
            const clear = fields.get("clear") === "on";
            // Secret input is uncontrolled and cleared before the request. Never put it in React/browser storage.
            const input = form.current?.elements.namedItem(
              "credential",
            ) as HTMLInputElement | null;
            if (input) input.value = "";
            try {
              const { id, credential_configured: _, ...profile } = draft;
              setData(
                await settingsRequest(
                  `/api/settings/providers/profiles/${encodeURIComponent(id)}`,
                  {
                    ...profile,
                    ...(credential ? { credential } : {}),
                    ...(clear ? { clear_credential: true } : {}),
                  },
                ),
              );
              setDraft(null);
              setSecretDirty(false);
              setStatus(
                "Provider profile saved. Agent assignments are unchanged.",
              );
            } catch {
              setSecretDirty(false);
              setError(
                "Profile could not be saved. Check fields and refresh saved profiles before retrying; re-enter a credential only to replace it.",
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="section-heading">
            <div>
              <h3>
                {original === JSON.stringify(blank)
                  ? "New provider profile"
                  : "Edit provider profile"}
              </h3>
              <p>
                Secrets are held on the server. Leaving the credential empty
                preserves its saved value.
              </p>
            </div>
          </div>
          <fieldset disabled={busy} className="settings-fields">
            <label>
              Profile ID
              <input
                value={draft.id}
                pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}"
                maxLength={64}
                required
                disabled={original !== JSON.stringify(blank)}
                onChange={(e) => setDraft({ ...draft, id: e.target.value })}
              />
            </label>
            <label>
              Display name
              <input
                value={draft.name}
                maxLength={200}
                required
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </label>
            <label>
              Provider
              <select
                aria-label="Provider"
                value={draft.provider}
                disabled={original !== JSON.stringify(blank)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    provider: e.target.value as Profile["provider"],
                    endpoint:
                      e.target.value === "openai"
                        ? "https://api.openai.com"
                        : "http://localhost:11434",
                    auth: e.target.value === "openai" ? "environment" : "none",
                  })
                }
              >
                <option value="ollama">Ollama</option>
                <option value="openai">OpenAI</option>
              </select>
            </label>
            <label>
              Endpoint
              <input
                type="url"
                value={draft.endpoint}
                required
                maxLength={500}
                readOnly={draft.provider === "openai"}
                onChange={(e) =>
                  setDraft({ ...draft, endpoint: e.target.value })
                }
              />
            </label>
            <label>
              Model ID
              <input
                list="discovered-models"
                value={draft.model}
                maxLength={200}
                required
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              />
              <datalist id="discovered-models">
                {models.map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </label>
            {draft.provider === "openai" && (
              <>
                <label>
                  Authentication
                  <select
                    aria-label="Authentication"
                    value={draft.auth}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        auth: e.target.value as Profile["auth"],
                      })
                    }
                  >
                    <option value="environment">Server OPENAI_API_KEY</option>
                    <option value="inline">Saved server credential</option>
                  </select>
                </label>
                <p>
                  {draft.auth === "environment"
                    ? data?.capabilities.openai_environment_configured
                      ? "Environment credential configured."
                      : "Environment credential not configured."
                    : draft.credential_configured
                      ? "Saved credential configured."
                      : "Saved credential not configured."}
                </p>
                {draft.auth === "inline" && (
                  <label>
                    Replace credential
                    <input
                      type="password"
                      name="credential"
                      autoComplete="new-password"
                      maxLength={8192}
                      onChange={() => setSecretDirty(true)}
                    />
                  </label>
                )}
                <label className="check-field">
                  <input
                    type="checkbox"
                    name="clear"
                    onChange={() => setSecretDirty(true)}
                  />{" "}
                  Clear saved credential (environment remains unchanged)
                </label>
              </>
            )}
          </fieldset>
          {dirty && <p role="status">Unsaved provider edits</p>}
          <div className="settings-toolbar">
            <button disabled={busy || !dirty}>
              {busy ? "Saving…" : "Save provider profile"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (!dirty || confirm("Discard unsaved provider edits?")) {
                  setDraft(null);
                  setSecretDirty(false);
                }
              }}
            >
              Cancel
            </button>
            {data?.profiles.some((p) => p.id === draft.id) && (
              <button
                type="button"
                disabled={busy || dirty}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    const result = await settingsRequest(
                      `/api/settings/providers/profiles/${encodeURIComponent(draft.id)}/discover`,
                      {},
                      "POST",
                    );
                    setModels(result.models);
                    setStatus(
                      result.models.length
                        ? `${result.models.length} model IDs discovered. Choose one in Model ID; discovery does not test inference.`
                        : "No models reported. Enter a model ID manually.",
                    );
                  } catch {
                    setError(
                      "Discovery unavailable. Enter a model ID manually.",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Discover models
              </button>
            )}
          </div>
        </form>
      )}
    </div>
  );
}

export function VoiceProviders({
  kind,
  onDirtyChange,
}: {
  kind: Backend["kind"];
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [backends, setBackends] = useState<Backend[] | null>(null);
  const [profiles, setProfiles] = useState<Profiles | null>(null);
  const [draft, setDraft] = useState<Backend | null>(null);
  const [original, setOriginal] = useState("");
  const [auth, setAuth] = useState("");
  const [originalAuth, setOriginalAuth] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [discovery, setDiscovery] = useState<{
    models: string[];
    voices: string[];
    message?: string;
  } | null>(null);
  const [defaults, setDefaults] = useState<Record<string, unknown>>({});
  const dirty =
    !!draft && (JSON.stringify(draft) !== original || auth !== originalAuth);
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
  async function load() {
    setError("");
    try {
      const [registry, p, speech] = await Promise.all([
        settingsRequest("/api/settings/speech"),
        settingsRequest("/api/settings/providers"),
        settingsRequest("/api/speech"),
      ]);
      setBackends(registry.providers);
      setProfiles(p);
      setDefaults(speech.defaults);
    } catch {
      setError("Voice configuration unavailable. Refresh to retry.");
    }
  }
  useEffect(() => {
    setDraft(null);
    setStatus("");
    void load();
  }, [kind]);
  const title =
    kind === "stt"
      ? "Speech to text"
      : kind === "tts"
        ? "Text to speech"
        : "Realtime voice";
  const key = kind === "realtime" ? "realtime_backend" : `${kind}_backend`;
  return (
    <div className="settings-stack">
      <div className="settings-intro">
        <span className="eyebrow">Voice / {kind.toUpperCase()}</span>
        <h2>{title}</h2>
        <p>
          {kind === "stt"
            ? "Turn speech into text with your configured Wyoming service."
            : kind === "tts"
              ? "Choose how an agent’s text becomes speech. Voices stay specific to each deployment."
              : "Configure the live audio model and voices. Realtime also requires server enablement, WebRTC and an eligible agent."}
        </p>
      </div>
      <div className="settings-toolbar">
        <button
          disabled={busy}
          onClick={() => {
            if (dirty && !confirm("Discard unsaved voice provider edits?"))
              return;
            setDraft({
              id: "",
              kind,
              adapter:
                kind === "stt"
                  ? "wyoming"
                  : kind === "tts"
                    ? "wyoming"
                    : "openai-live",
              model: "",
              host: kind === "realtime" ? "api.openai.com" : "localhost",
              port: kind === "stt" ? 10300 : kind === "tts" ? 10200 : 443,
              voices:
                kind === "stt" ? [] : kind === "realtime" ? ["marin"] : [""],
            });
            setOriginal("");
            setAuth("");
            setOriginalAuth("");
            setDiscovery(null);
          }}
        >
          + Add voice provider
        </button>
        <button disabled={busy} onClick={() => void load()}>
          Refresh voice providers
        </button>
        <span className="pill">
          {backends?.filter((b) => b.kind === kind).length ?? "…"} deployments
        </span>
      </div>
      {error && <p role="alert">{error}</p>}
      {status && <p role="status">{status}</p>}
      {!backends && !error && <p role="status">Loading voice providers…</p>}
      {backends && (
        <section className="card settings-form">
          <h3>Global {kind.toUpperCase()} default</h3>
          <p>
            Applies to new turns that inherit this default. Agent and device
            overrides are retained.
          </p>
          <label>
            Default deployment
            <select
              aria-label="Default deployment"
              disabled={busy}
              value={String(defaults[key] || "")}
              onChange={async (e) => {
                const id = e.target.value;
                setBusy(true);
                setError("");
                try {
                  const b = backends.find((b) => b.id === id)!;
                  const patch = {
                    [key]: id,
                    ...(kind === "realtime"
                      ? { realtime_voice: b.voices[0] }
                      : {}),
                  };
                  const next = await settingsRequest(
                    "/api/speech",
                    patch,
                    "PATCH",
                  );
                  setDefaults(next.defaults);
                  setStatus("Global voice default saved.");
                  window.dispatchEvent(new Event("speech-settings-changed"));
                } catch {
                  setError(
                    "Default could not be saved. Stop active voice and refresh before retrying.",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              {backends
                .filter((b) => b.kind === kind)
                .map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.id} · {b.model}
                  </option>
                ))}
            </select>
          </label>
          {kind !== "stt" && backends.find((b) => b.id === defaults[key]) && (
            <label>
              Default voice
              <select
                aria-label="Default voice"
                disabled={busy}
                value={
                  kind === "realtime"
                    ? String(defaults.realtime_voice || "")
                    : String(
                        ((defaults.voices as Record<string, string>) || {})[
                          String(defaults[key])
                        ] ||
                          backends.find((b) => b.id === defaults[key])
                            ?.voices[0] ||
                          "",
                      )
                }
                onChange={async (e) => {
                  const voice = e.target.value;
                  setBusy(true);
                  setError("");
                  try {
                    const next = await settingsRequest(
                      "/api/speech",
                      kind === "realtime"
                        ? { realtime_voice: voice }
                        : { voices: { [String(defaults[key])]: voice } },
                      "PATCH",
                    );
                    setDefaults(next.defaults);
                    setStatus("Global voice default saved.");
                    window.dispatchEvent(new Event("speech-settings-changed"));
                  } catch {
                    setError(
                      "Voice default could not be saved. Refresh before retrying.",
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {backends
                  .find((b) => b.id === defaults[key])
                  ?.voices.map((v) => (
                    <option key={v}>{v}</option>
                  ))}
              </select>
            </label>
          )}
        </section>
      )}
      <div className="provider-grid">
        {backends
          ?.filter((b) => b.kind === kind)
          .map((b) => (
            <button
              key={b.id}
              className="provider-tile"
              aria-pressed={draft?.id === b.id}
              onClick={() => {
                if (dirty && !confirm("Discard unsaved voice provider edits?"))
                  return;
                setDraft({ ...b });
                setOriginal(JSON.stringify(b));
                setAuth(profiles?.speech_auth[b.id] || "");
                setOriginalAuth(profiles?.speech_auth[b.id] || "");
                setDiscovery(null);
                setStatus("");
              }}
            >
              <span className="eyebrow">{b.adapter}</span>
              <strong>{b.id}</strong>
              <span>{b.model}</span>
              <small>
                {b.host}:{b.port}
              </small>
            </button>
          ))}
      </div>
      {draft && (
        <form
          className="card settings-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              if (JSON.stringify(draft) !== original)
                await settingsRequest(
                  `/api/settings/speech/${encodeURIComponent(draft.id)}`,
                  draft,
                );
              if (auth !== originalAuth)
                await settingsRequest("/api/settings/providers/speech-auth", {
                  backend_id: draft.id,
                  profile_id: auth || null,
                });
              setOriginal(JSON.stringify(draft));
              setOriginalAuth(auth);
              setStatus(
                "Voice provider saved. Applies to new turns; reconnect active WebRTC sessions.",
              );
              await load();
              window.dispatchEvent(new Event("speech-settings-changed"));
            } catch {
              setError(
                "Provider change could not be completed. Stop active voice and refresh stored settings before retrying; some changes may have saved.",
              );
            } finally {
              setBusy(false);
            }
          }}
        >
          <h3>{draft.id || "New voice provider"}</h3>
          <p>
            Deployment ID and adapter remain stable for existing agent and
            device references.
          </p>
          <fieldset disabled={busy} className="settings-fields">
            {original === "" && (
              <>
                <label>
                  Deployment ID
                  <input
                    value={draft.id}
                    required
                    maxLength={64}
                    pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}"
                    onChange={(e) => setDraft({ ...draft, id: e.target.value })}
                  />
                </label>
                <label>
                  Adapter
                  <select
                    value={draft.adapter}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        adapter: e.target.value,
                        ...(e.target.value.startsWith("openai")
                          ? { host: "api.openai.com", port: 443 }
                          : {}),
                      })
                    }
                  >
                    {(kind === "stt"
                      ? ["wyoming", "whisper", "parakeet-v3"]
                      : kind === "tts"
                        ? ["wyoming", "piper", "kokoro", "openai"]
                        : ["openai-live"]
                    ).map((a) => (
                      <option key={a}>{a}</option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <label>
              Model
              <input
                value={draft.model}
                required
                maxLength={200}
                list="speech-models"
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
              />
            </label>
            <datalist id="speech-models">
              {discovery?.models.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <label>
              Host
              <input
                value={draft.host}
                required
                maxLength={253}
                readOnly={draft.adapter.startsWith("openai")}
                onChange={(e) => setDraft({ ...draft, host: e.target.value })}
              />
            </label>
            <label>
              Port
              <input
                type="number"
                min={1}
                max={65535}
                required
                value={draft.port}
                readOnly={draft.adapter.startsWith("openai")}
                onChange={(e) =>
                  setDraft({ ...draft, port: Number(e.target.value) })
                }
              />
            </label>
            {kind !== "stt" && (
              <label className="field-wide">
                Configured voice IDs (one per line)
                <textarea
                  rows={4}
                  value={draft.voices.join("\n")}
                  onChange={(e) =>
                    setDraft({ ...draft, voices: e.target.value.split("\n") })
                  }
                />
                <small>
                  Referenced voices must remain available. Discovery suggestions
                  never overwrite your configuration.
                </small>
              </label>
            )}
            {draft.adapter === "openai" && (
              <label>
                Credential profile
                <select value={auth} onChange={(e) => setAuth(e.target.value)}>
                  <option value="">Server OPENAI_API_KEY</option>
                  {profiles?.profiles
                    .filter((p) => p.provider === "openai")
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ·{" "}
                        {p.credential_configured
                          ? "configured"
                          : "credential missing"}
                      </option>
                    ))}
                </select>
                <small>
                  Create or update shared credentials in Model providers.
                </small>
              </label>
            )}
          </fieldset>
          {kind === "realtime" && (
            <p>
              Authentication: server OPENAI_API_KEY ·{" "}
              {profiles?.capabilities.openai_environment_configured
                ? "configured"
                : "not configured"}
              . Realtime uses environment authentication; saved profiles
              currently apply to Standard TTS and model connections.
            </p>
          )}
          {dirty && <p role="status">Unsaved voice provider edits</p>}
          <div className="settings-toolbar">
            <button disabled={busy || !dirty}>
              {busy ? "Saving…" : "Save voice provider"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (!dirty || confirm("Discard unsaved voice provider edits?"))
                  setDraft(null);
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={busy || dirty || !draft.id || !original}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  const result = await settingsRequest(
                    `/api/settings/speech/${encodeURIComponent(draft.id)}/discover`,
                    {},
                    "POST",
                  );
                  setDiscovery(result);
                  setStatus(
                    result.message ||
                      "Discovery complete. Review suggestions before editing; nothing has been saved.",
                  );
                } catch {
                  setError(
                    "Discovery unavailable. Enter model and voice IDs manually.",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              Discover models & voices
            </button>
          </div>
          {discovery && (
            <div className="discovery-results">
              <h4>Discovery results</h4>
              <p>
                Models: {discovery.models.join(", ") || "No model IDs reported"}
              </p>
              {kind !== "stt" && (
                <p>
                  Voices: {discovery.voices.join(", ") || "No voices reported"}
                </p>
              )}
            </div>
          )}
        </form>
      )}
      <p className="settings-footnote">
        Discovery only reads service metadata. Models are installed and services
        enabled outside Vauxr; no inference or audio is started here.
      </p>
    </div>
  );
}
