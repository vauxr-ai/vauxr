import LoadingIndicator from "./LoadingIndicator";
import { useEffect, useState } from "react";
import { ownerFetch, jsonResponse } from "../auth/api";

type Profile = { id: string; name: string; provider: string };
const statusText = (status: string) => status === "unsupported"
  ? "Unsupported: OpenAI profiles cannot run this agent. Choose an Ollama profile. No model request was made."
  : status === "setup_needed" ? "Setup needed. Configure an available model runtime."
  : "Configured. Inference readiness has not been tested.";

export default function SupportSettings({agentId}:{agentId?:string}) {
  const endpointPath = agentId ? `/api/local-agents/${encodeURIComponent(agentId)}/runtime` : "/api/support";
  const [provider, setProvider] = useState("");
  const [profileId, setProfileId] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("Loading runtime settings…");
  const [busy, setBusy] = useState(true);
  useEffect(() => { const control = new AbortController();
    setBusy(true);
    ownerFetch("/api/settings/providers", { signal: control.signal }).then(jsonResponse)
      .then(data => setProfiles(data.profiles ?? []))
      .catch(() => { if (!control.signal.aborted) setProfiles([]); });
    ownerFetch(endpointPath, { signal: control.signal }).then(jsonResponse).then(data => {
      setProvider(data.profile_id ? "profile" : data.provider ?? "");
      setProfileId(data.profile_id ?? ""); setEndpoint(data.endpoint ?? ""); setModel(data.model ?? "");
      setStatus(statusText(data.status));
    }).catch(() => { if (!control.signal.aborted) setStatus("Runtime settings unavailable. Refresh before retrying."); })
      .finally(() => { if (!control.signal.aborted) setBusy(false); });
    return () => control.abort();
  }, [endpointPath]);
  return <section aria-label={agentId ? "Agent model runtime" : "Support runtime"} className="space-y-3">
    <h3>{agentId ? "Agent model runtime" : "Independent Support runtime"}</h3>
    <p>Registered LangChain agent. No OpenClaw connection required. Choose a saved model profile from Settings → Model providers or configure a direct local Ollama runtime. No provider or model is selected automatically. OpenAI agent execution is unsupported.</p>
    {busy && status === "Loading runtime settings…" ? <LoadingIndicator label="Loading runtime settings"/> : <p role="status">{status}</p>}
    <form className="agent-runtime-form grid gap-3" onSubmit={async event => {
      event.preventDefault(); setBusy(true);
      try {
        const body = provider === "profile" ? {profile_id: profileId} : provider ? {provider, endpoint, model} : {provider:"",endpoint:"",model:""};
        const data = await jsonResponse(await ownerFetch(endpointPath, { method: "POST", body: JSON.stringify(body) }));
        setStatus(`Saved. ${statusText(data.status)}`);
      } catch { setStatus("Save unavailable or invalid runtime configuration. Refresh before retrying."); }
      finally { setBusy(false); }
    }}>
      <label>{agentId ? "Model provider" : "Support provider"}<select aria-label={agentId ? "Model provider" : "Support provider"} value={provider} onChange={e => setProvider(e.target.value)} disabled={busy}>
        <option value="">Not configured</option><option value="profile">Saved model profile</option><option value="ollama">Local Ollama runtime</option>
      </select></label>
      {provider === "profile" && <label>Model profile<select aria-label="Model profile" value={profileId} onChange={e => setProfileId(e.target.value)} required disabled={busy}>
        <option value="">Choose a profile</option>
        {profileId && !profiles.some(p => p.id === profileId) && <option value={profileId}>Unavailable profile</option>}
        {profiles.map(p => <option key={p.id} value={p.id}>{p.name}{p.provider === "openai" ? " (unsupported for agents)" : ""}</option>)}
      </select></label>}
      {provider === "ollama" && <><label>Local endpoint<input value={endpoint} onChange={e => setEndpoint(e.target.value)} placeholder="http://127.0.0.1:11434" required disabled={busy} /></label>
        <label>Installed local model<input value={model} onChange={e => setModel(e.target.value)} required maxLength={120} disabled={busy} /></label></>}
      <div className="modal-actions"><button disabled={busy}>{agentId ? "Save agent runtime" : "Save Support runtime"}</button></div>
    </form>
  </section>;
}
