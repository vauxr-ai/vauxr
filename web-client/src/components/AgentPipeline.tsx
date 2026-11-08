import "./AgentPipeline.css";

function Entity({kind, title, detail, onEdit}: {kind: string; title: string; detail?: string; onEdit?: () => void}) {
  return <div className="pipeline-entity">
    <span className="pipeline-icon"><svg aria-hidden="true" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {kind === "device" ? <><rect x="8" y="3" width="16" height="26" rx="5"/><path d="M13 7h6M14 25h4M12 14v4m4-7v10m4-7v4"/></> : kind === "agent" ? <><path d="m16 3 12 7v13l-12 7-12-7V10Z"/><path d="m4 10 12 7 12-7M16 17v13M10 7l12 7"/><circle cx="16" cy="10" r="2"/></> : kind === "stt" ? <><rect x="3" y="4" width="26" height="24" rx="7"/><path d="M9 10v8m-3-5v2m6-3v4m5-5h7m-7 5h5M9 23h14"/></> : kind === "tts" ? <><path d="M16 6 8 12H3v8h5l8 6ZM21 11c3 3 3 7 0 10m4-14c6 5 6 13 0 18"/></> : <><rect x="3" y="5" width="26" height="22" rx="7"/><path d="M8 14v4m4-8v12m4-9v6m4-10v14m4-9v4"/></>}
    </svg></span>
    <strong>{title}</strong><small>{detail || "Not configured"}</small>{kind === "device" && <details className="pipeline-device-info"><summary className="pipeline-edit" aria-label="Voice Device details">Details</summary><small>Set microphone, speaker and voice overrides for individual devices in Devices.</small></details>}{onEdit && <button type="button" className="pipeline-edit" onClick={onEdit} aria-label={`Edit ${title}`}>Edit <span aria-hidden="true">↗</span></button>}
  </div>;
}
export default function AgentPipeline({mode, agentName, stt, tts, realtime, enabled, isDefault, onEdit}: {mode: string; agentName: string; stt?: string; tts?: string; realtime?: string; enabled: boolean; isDefault: boolean; onEdit?: (kind: string) => void}) {
  const live = mode === "realtime";
  return <div className="agent-pipeline" aria-label={live ? "Realtime voice pipeline" : "Standard voice pipeline"}>
    <div className="pipeline-state"><span className={enabled ? "enabled" : ""}>{enabled ? "Enabled" : "Disabled"}</span>{isDefault && enabled && <span className="default">Default</span>}</div>
    <div className="pipeline-row">
      <Entity kind="device" title="Voice Device" detail="Audio input & output"/>
      <span className="pipeline-arrow" aria-label={live ? "bidirectional" : "to"}>{live ? "⇄" : "→"}</span>
      <Entity onEdit={() => onEdit?.(live ? "realtime" : "stt")} kind={live ? "realtime" : "stt"} title={live ? "Realtime Provider" : "Speech To Text"} detail={live ? realtime : stt}/>
      <span className="pipeline-arrow" aria-label={live ? "bidirectional" : "to"}>{live ? "⇄" : "→"}</span>
      <Entity onEdit={() => document.querySelector<HTMLButtonElement>('[aria-label="Edit agent name"]')?.click()} kind="agent" title={agentName} detail="Agent"/>
    </div>
    {!live && <div className="pipeline-row pipeline-return">
      <Entity kind="device" title="Voice Device" detail="Spoken response"/>
      <span className="pipeline-arrow" aria-label="from">←</span>
      <Entity onEdit={() => onEdit?.("tts")} kind="tts" title="Text To Speech" detail={tts}/>
      <span className="pipeline-arrow" aria-label="from">←</span>
      <Entity onEdit={() => document.querySelector<HTMLButtonElement>('[aria-label="Edit agent name"]')?.click()} kind="agent" title={agentName} detail="Agent response"/>
    </div>}
  </div>;
}
