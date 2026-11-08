import { useEffect, useState } from "react";
import { ownerFetch, jsonResponse } from "../auth/api";

export default function SupportSettings() {
  const [provider, setProvider] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("Loading Support settings…");
  const [busy, setBusy] = useState(true);
  useEffect(() => { const control = new AbortController();
    ownerFetch("/api/support", { signal: control.signal }).then(jsonResponse).then(data => {
      setProvider(data.provider); setEndpoint(data.endpoint); setModel(data.model);
      setStatus(data.status === "setup_needed" ? "Setup needed. No model runtime is configured." : "Configured. Inference readiness has not been tested.");
    }).catch(() => { if (!control.signal.aborted) setStatus("Support settings unavailable. Refresh before retrying."); })
      .finally(() => { if (!control.signal.aborted) setBusy(false); });
    return () => control.abort();
  }, []);
  return <section aria-label="Support runtime" className="space-y-3">
    <h3>Independent Support runtime</h3>
    <p>Built in and available without OpenClaw. Configure a separately installed local runtime and local model. No provider or model is selected automatically. Advice only; it cannot claim to execute settings changes.</p>
    <p role="status">{status}</p>
    <form className="grid gap-3" onSubmit={async event => {
      event.preventDefault(); setBusy(true);
      try { await jsonResponse(await ownerFetch("/api/support", { method: "POST", body: JSON.stringify(provider ? {provider, endpoint, model} : {provider:"",endpoint:"",model:""}) }));
        setStatus(provider ? "Saved. Inference readiness has not been tested." : "Setup needed. Runtime configuration cleared.");
      } catch { setStatus("Save unavailable or invalid local configuration. Refresh status before retrying."); }
      finally { setBusy(false); }
    }}>
      <label>Support provider<select value={provider} onChange={e => setProvider(e.target.value)} disabled={busy}>
        <option value="">Not configured</option><option value="ollama">Local Ollama runtime</option>
      </select></label>
      {provider && <><label>Local endpoint<input value={endpoint} onChange={e => setEndpoint(e.target.value)} placeholder="http://127.0.0.1:11434" required disabled={busy} /></label>
        <label>Installed local model<input value={model} onChange={e => setModel(e.target.value)} required maxLength={120} disabled={busy} /></label></>}
      <button disabled={busy}>Save Support runtime</button>
    </form>
  </section>;
}
