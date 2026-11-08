import "./AgentPipeline.css";
import type { MouseEvent, ReactNode } from "react";

function Entity({kind, title, detail, onEdit, icon}: {kind: string; title: string; detail?: string; icon?: ReactNode; onEdit?: (event: MouseEvent<HTMLButtonElement>) => void}) {
  const content = <>
    {!icon && <span className="pipeline-icon"><svg aria-hidden="true" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {kind === "device" ? <><rect x="8" y="3" width="16" height="26" rx="5"/><path d="M13 7h6M14 25h4M12 14v4m4-7v10m4-7v4"/></> : kind === "agent" ? <><path d="m16 3 12 7v13l-12 7-12-7V10Z"/><path d="m4 10 12 7 12-7M16 17v13M10 7l12 7"/><circle cx="16" cy="10" r="2"/></> : kind === "stt" ? <><rect x="3" y="4" width="26" height="24" rx="7"/><path d="M9 10v8m-3-5v2m6-3v4m5-5h7m-7 5h5M9 23h14"/></> : kind === "tts" ? <><path d="M16 6 8 12H3v8h5l8 6ZM21 11c3 3 3 7 0 10m4-14c6 5 6 13 0 18"/></> : <><rect x="3" y="5" width="26" height="22" rx="7"/><path d="M8 14v4m4-8v12m4-9v6m4-10v14m4-9v4"/></>}
    </svg></span>}
    </>;
  const label = kind === "agent" ? title : detail || "Not configured";
  return <div className="pipeline-entity">{icon || content}
    {kind !== "agent" && <strong>{title}</strong>}
    {onEdit ? <button type="button" className="pipeline-entity-label" onClick={onEdit} aria-label={`Edit ${title}`} aria-haspopup="dialog">{label}</button> : kind === "agent" ? <strong>{title}</strong> : <small>{label}</small>}
    {kind === "agent" && <small>Agent</small>}
    {kind === "device" && <details className="pipeline-device-info"><summary className="pipeline-edit" aria-label="Voice Device details">Details</summary><small>Set microphone, speaker and voice overrides for individual devices in Devices.</small></details>}
  </div>;
}
export type PipelineGraphProps = {
  mode: string;
  ttsIcon?: ReactNode;
  deviceLabel?: string;
  deviceDetail?: string;
  agentName: string;
  stt?: string;
  tts?: string;
  realtime?: string;
  onEdit?: (kind: "stt" | "tts" | "realtime", anchor: HTMLButtonElement) => void;
  onEditDevice?: () => void;
  onEditAgent?: (event: MouseEvent<HTMLButtonElement>) => void;
};

function Connections({side, live}: {side: "left" | "right"; live: boolean}) {
  return <svg className={`pipeline-connections pipeline-connections-${side}`} viewBox="0 0 100 200" preserveAspectRatio="none" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {live ? <><path d="M2 100H98M10 94l-8 6 8 6M90 94l8 6-8 6"/></> : side === "left" ? <>
      <path d="M0 100H24Q38 100 38 86V64Q38 50 52 50H98M90 44l8 6-8 6"/>
      <path d="M98 150H52Q38 150 38 136V114Q38 100 24 100H2M10 94l-8 6 8 6"/>
    </> : <>
      <path d="M2 50H48Q62 50 62 64V86Q62 100 76 100H98M90 94l8 6-8 6"/>
      <path d="M100 100H76Q62 100 62 114V136Q62 150 48 150H2M10 144l-8 6 8 6"/>
    </>}
  </svg>;
}

/** A single connected graph; endpoint identity is preserved at every viewport size. */
export function PipelineGraph({mode, deviceLabel = "Voice Device", deviceDetail = "Audio input & output", agentName, stt, tts, realtime, onEdit, onEditDevice, onEditAgent, ttsIcon}: PipelineGraphProps) {
  const live = mode === "realtime";
  return <div className={`pipeline-graph ${live ? "pipeline-graph-realtime" : "pipeline-graph-standard"}`} role="group" aria-label={live ? "Realtime voice pipeline" : "Standard voice pipeline"}>
    <p className="pipeline-flow-description">{live ? `${deviceLabel} ↔ Realtime Provider ↔ ${agentName}` : `${deviceLabel} → Speech To Text → ${agentName} → Text To Speech → ${deviceLabel}`}</p>
    <div className="pipeline-endpoint pipeline-device"><Entity kind="device" title={deviceLabel} detail={deviceDetail} onEdit={onEditDevice}/></div>
    <Connections side="left" live={live}/>
    <div className="pipeline-provider pipeline-input"><Entity kind={live ? "realtime" : "stt"} title={live ? "Realtime Provider" : "Speech To Text"} detail={live ? realtime : stt} onEdit={onEdit ? event => onEdit(live ? "realtime" : "stt", event.currentTarget) : undefined}/></div>
    {!live && <div className="pipeline-provider pipeline-output"><Entity kind="tts" icon={ttsIcon} title="Text To Speech" detail={tts} onEdit={onEdit ? event => onEdit("tts", event.currentTarget) : undefined}/></div>}
    <Connections side="right" live={live}/>
    <div className="pipeline-endpoint pipeline-agent"><Entity kind="agent" title={agentName} detail="Agent" onEdit={onEditAgent}/></div>
  </div>;
}

export default function AgentPipeline({enabled, isDefault, onToggleEnabled, onSetDefault, ...graph}: PipelineGraphProps & {enabled: boolean; isDefault: boolean; onToggleEnabled: () => void; onSetDefault: () => void}) {
  const label = graph.mode === "realtime" ? "Live" : "Standard";
  return <div className="agent-pipeline">
    <div className="pipeline-state">
      <button type="button" aria-label={`Enable ${label}`} aria-pressed={enabled} className={enabled ? "enabled" : ""} onClick={onToggleEnabled}>{enabled ? "Enabled" : "Disabled"}</button>
      <button type="button" aria-label={`Default ${label}`} aria-pressed={enabled && isDefault} className={enabled && isDefault ? "default" : ""} disabled={!enabled} onClick={onSetDefault}>{isDefault && enabled ? "Default" : "Set as default"}</button>
    </div>
    <PipelineGraph {...graph}/>
  </div>;
}
