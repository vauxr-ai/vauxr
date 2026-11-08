import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ownerFetch } from "../auth/api";
import AgentPipeline from "./AgentPipeline";
import RefreshButton from "./RefreshButton";

type Backend = { id: string; kind: "stt" | "tts" | "realtime"; model: string; voices: string[]; readiness: string };
type Selection = { mode?: string | null; stt_backend?: string; tts_backend?: string; realtime_backend?: string; realtime_voice?: string; voices?: Record<string, string> };
type View = {
  defaults: Selection; overrides: Selection; backends: Backend[];
  effective: { stt_backend: string; tts_backend: string; voice_id: string } | null;
  voice?: { mode: "standard" | "realtime" | null; realtime_backend: string; realtime_voice: string };
  error?: string | null;
  policy: { assigned: boolean; agent_id: string; fallback_agent?: string; agents: { id: string; name: string }[]; available_modes: string[]; conflict?: string | null; chat_only?: boolean };
};

/** Device-only editor; writes are serialized and reads cannot replace newer edits. */
export default function DeviceVoicePipeline({ deviceId, onModeChange, transport }: {
  deviceId: string; onModeChange: (mode: "standard" | "realtime" | null) => void; transport: ReactNode;
}) {
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const version = useRef(0);
  const writes = useRef(Promise.resolve());
  const mounted = useRef(true);
  const path = `/api/devices/${encodeURIComponent(deviceId)}/speech`;
  const request = useCallback(async (patch?: object) => {
    const response = await ownerFetch(path, { method: patch ? "PATCH" : "GET", headers: { "Content-Type": "application/json" }, ...(patch ? { body: JSON.stringify(patch) } : {}) });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Unable to save voice configuration");
    if (!body.defaults || !body.policy || !Array.isArray(body.backends)) throw new Error("Invalid voice configuration response");
    return body as View;
  }, [path]);
  const refresh = useCallback(async () => {
    const generation = ++version.current;
    setBusy(true);
    try { const next = await request(); if (mounted.current && generation === version.current) { setData(next); setError(""); } }
    catch (err) { if (mounted.current && generation === version.current) setError((err as Error).message); }
    finally { if (mounted.current && generation === version.current) setBusy(false); }
  }, [request]);
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; ++version.current; }; }, [refresh]);
  useEffect(() => { if (data) onModeChange(data.policy.chat_only ? null : data.voice?.mode || "standard"); }, [data, onModeChange]);
  function save(patch: object) {
    const generation = ++version.current;
    setBusy(true); setStatus("Saving…"); setError("");
    writes.current = writes.current.catch(() => {}).then(async () => {
      try {
        const next = await request(patch);
        if (mounted.current && generation === version.current) { setData(next); setStatus("Saved"); }
        window.dispatchEvent(new CustomEvent("speech-settings-changed", { detail: { source: "settings", scope: "device", deviceId } }));
      } catch (err) { if (mounted.current && generation === version.current) { setError((err as Error).message); setStatus("Changes not saved"); } }
      finally { if (mounted.current && generation === version.current) setBusy(false); }
    });
  }
  const mode = data?.voice?.mode || "standard";
  const tts = data?.backends.find(b => b.id === data.effective?.tts_backend);
  const realtime = data?.backends.find(b => b.id === data.voice?.realtime_backend);
  const agent = data?.policy.agents.find(a => a.id === data.policy.agent_id);
  function select(label: string, value: string, inherited: string | undefined, options: { value: string; label: string }[], onChange: (value: string) => void) {
    return <label className="device-field speech-field">{label}<select aria-label={label} value={value} onChange={event => onChange(event.target.value)}><option value="">Use agent default{inherited ? ` (${inherited})` : ""}</option>{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>;
  }
  const backendOptions = (kind: string) => (data?.backends || []).filter(b => b.kind === kind).map(b => ({ value: b.id, label: `${b.id} · ${b.model} · ${b.readiness}` }));
  return <section className="device-section device-assignment" aria-label="Assigned agent">
    <div className="device-section-heading"><h3>Assigned agent</h3><span role="status">{status}</span><RefreshButton label="Refresh device voice configuration" busy={busy} onClick={() => void refresh()} /></div>
    {!data && !error && <p role="status">Loading voice configuration…</p>}
    {error && <p role="alert">{error}</p>}
    {data && <>
      <label className="device-field"><span className="sr-only">Assigned agent</span><select aria-label="Assigned agent" disabled={busy} value={data.policy.assigned ? data.policy.agent_id : ""} onChange={event => save({ agent_id: event.target.value || null })}>
        <option value="">Use default agent ({data.policy.fallback_agent || "none"})</option>
        {data.policy.assigned && !agent && <option value={data.policy.agent_id}>Unavailable agent ({data.policy.agent_id})</option>}
        {data.policy.agents.map(a => <option value={a.id} key={a.id}>{a.name}</option>)}
      </select></label>
      {data.policy.conflict && <p role="alert">{data.policy.conflict}</p>}
      {data.error && <p role="alert">{data.error}</p>}
      <div className="device-pipeline-subsection">
        <h4>Voice pipeline</h4>
        <p className="device-muted">{Object.keys(data.overrides).length ? "This device has custom voice settings." : "Using the assigned agent’s voice defaults."}</p>
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Hide voice pipeline overrides" : "Override agent voice pipeline defaults"}</button>
        {expanded && <fieldset disabled={busy} className="device-voice-overrides speech-fields">
          {data.policy.chat_only ? <p>Chat-only agent. Select a voice-enabled agent to use this device’s microphone and speaker.</p> : <>
            <AgentPipeline mode={mode} agentName={agent?.name || data.policy.fallback_agent || "Agent"} stt={data.effective?.stt_backend} tts={data.effective?.tts_backend} realtime={data.voice?.realtime_backend} enabled isDefault={false} />
            {data.effective && mode !== "realtime" && <p className="device-muted">Effective: {data.effective.stt_backend} / {data.effective.tts_backend} / {data.effective.voice_id}</p>}
            {transport}
            {select("Voice mode", data.overrides.mode || "", data.defaults.mode === "realtime" ? "Live" : "Standard", [...new Set([...data.policy.available_modes, ...(data.overrides.mode ? [data.overrides.mode] : [])])].map(value => ({value, label: value === "realtime" ? "Live / Realtime" : "Standard"})), value => save({ mode: value || null }))}
            <div className="device-provider-grid">
              {mode === "realtime" ? <>
                {select("Realtime provider / model", data.overrides.realtime_backend || "", data.defaults.realtime_backend, backendOptions("realtime"), value => save({ realtime_backend: value || null, realtime_voice: value ? data.backends.find(b => b.id === value)?.voices[0] : null }))}
                {select("Realtime voice", data.overrides.realtime_voice || "", data.defaults.realtime_voice, (realtime?.voices || []).map(value => ({value, label:value})), value => save({ realtime_voice:value || null }))}
              </> : <>
                {(["stt", "tts"] as const).map(kind => <div key={kind}>{select(`${kind.toUpperCase()} backend`, data.overrides[`${kind}_backend`] || "", data.defaults[`${kind}_backend`], backendOptions(kind), value => save({ [`${kind}_backend`]: value || null }))}</div>)}
                {tts && select("Voice", data.overrides.voices?.[tts.id] || "", data.defaults.voices?.[tts.id] || tts.voices[0], tts.voices.map(value => ({value, label:value})), value => save({voices:{[tts.id]:value || null}}))}
              </>}
            </div>
          </>}
          <button type="button" onClick={() => save({reset:true})}>Reset voice overrides</button>
          <p className="device-muted">Changes save automatically. Stop voice before changing agents or modes; reconnect for transport and provider changes.</p>
        </fieldset>}
      </div>
    </>}
  </section>;
}
