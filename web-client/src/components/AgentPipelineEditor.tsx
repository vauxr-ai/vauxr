import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import AgentPipeline from "./AgentPipeline";
import RefreshButton from "./RefreshButton";
import { ownerFetch, jsonResponse } from "../auth/api";
import type { Backend, Defaults, Policy } from "./SpeechSettings";

type EntityKind = "stt" | "tts" | "realtime" | "agent";
type Profile = {id: string; name: string; provider: string; model?: string};
type Runtime = {profile_id?: string; provider?: string; model?: string};

function Choices({label, value, options, onChange}: {label: string; value: string; options: {value: string; label: string; detail?: string; disabled?: boolean}[]; onChange: (value: string) => void}) {
  const name = useId();
  return <fieldset className="pipeline-choices"><legend>{label}</legend>
    {!options.length && <p>No configured choices available.</p>}
    {options.map(option => <label className="pipeline-choice" key={option.value}>
      <input type="radio" name={name} value={option.value} checked={value === option.value} disabled={option.disabled} onChange={() => onChange(option.value)}/>
      <span><strong>{option.label}</strong>{option.detail && <small>{option.detail}</small>}</span>
    </label>)}
  </fieldset>;
}

/** Stay inside the native modal's top layer; a body portal would become inert. */
function EntityPopover({anchor, title, children, onClose}: {anchor: HTMLButtonElement; title: string; children: ReactNode; onClose: () => void}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose); close.current = onClose;
  useLayoutEffect(() => {
    const node = ref.current!;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(340, window.innerWidth - 24);
      node.style.width = `${width}px`;
      node.style.left = `${Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 12))}px`;
      const height = node.getBoundingClientRect().height;
      node.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - height - 12))}px`;
    };
    place();
    const resize = new ResizeObserver(place); resize.observe(node);
    (node.querySelector<HTMLInputElement>('input:checked:not(:disabled)') || node.querySelector<HTMLButtonElement>('button'))?.focus();
    const outside = (event: PointerEvent) => { if (!node.contains(event.target as Node) && !anchor.contains(event.target as Node)) close.current(); };
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); anchor.focus(); }
      if (event.key !== "Tab") return;
      event.stopPropagation();
      const all = Array.from(node.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')).filter(el => el.getClientRects().length);
      // Native radio groups contribute only their selected (or first) radio to Tab order.
      const focusable = all.filter(el => !(el instanceof HTMLInputElement) || el.type !== "radio" || el.checked || !all.some(other => other instanceof HTMLInputElement && other.name === el.name && other.checked) && all.find(other => other instanceof HTMLInputElement && other.name === el.name) === el);
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (!node.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", keydown, true);
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => { resize.disconnect(); document.removeEventListener("keydown", keydown, true); document.removeEventListener("pointerdown", outside, true); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [anchor]);
  return createPortal(<div ref={ref} role="dialog" aria-label={title} aria-labelledby={titleId} className="pipeline-popover">
    <header><h4 id={titleId}>{title}</h4><button type="button" aria-label="Close entity settings" onClick={() => { onClose(); anchor.focus(); }}>×</button></header>
    {children}
  </div>, anchor.closest("dialog") || document.body);
}

export default function AgentPipelineEditor({agentId, agentType, agentName, policy, defaults, backends, busy, mode, onModeChange, onDraft, onSave, onRefresh, preview, hasProviderDraft, saved}: {
  hasProviderDraft: boolean; saved: string;
  agentId: string; agentType?: string; agentName: string; policy: Policy; defaults: Defaults; backends: Backend[]; busy: boolean; mode: string;
  onModeChange: (mode: string) => void; onDraft: (patch: Partial<Defaults>) => void; onSave: (patch: object) => Promise<boolean | undefined>; onRefresh: () => void; preview: ReactNode;
}) {
  const id = useId();
  const [enabled, setEnabled] = useState(policy.enabled_modes);
  const [defaultMode, setDefaultMode] = useState(policy.default_mode);
  const [open, setOpen] = useState<{kind: EntityKind; anchor: HTMLButtonElement} | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [profileDraft, setProfileDraft] = useState<string | null>(null);
  const [runtimeError, setRuntimeError] = useState("");
  const [runtimeLoading, setRuntimeLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const dirty = hasProviderDraft || profileDraft !== null || JSON.stringify(enabled) !== JSON.stringify(policy.enabled_modes) || defaultMode !== policy.default_mode;
  const local = ["support", "langgraph", "langchain"].includes(agentType || "");
  const runtimePath = agentType === "support" ? "/api/support" : `/api/local-agents/${encodeURIComponent(agentId)}/runtime`;
  useEffect(() => { setEnabled(policy.enabled_modes); setDefaultMode(policy.default_mode); }, [policy]);
  useEffect(() => {
    if (open?.kind !== "agent" || !local) return;
    const control = new AbortController();
    setRuntimeLoading(true); setRuntimeError("");
    Promise.all([ownerFetch("/api/settings/providers", {signal: control.signal}).then(jsonResponse), ownerFetch(runtimePath, {signal: control.signal}).then(jsonResponse)])
      .then(([settings, current]) => { if (!control.signal.aborted) { setProfiles(settings.profiles || []); setRuntime(current); } })
      .catch(err => { if (!control.signal.aborted) setRuntimeError(err instanceof Error ? err.message : "Model choices unavailable."); })
      .finally(() => { if (!control.signal.aborted) setRuntimeLoading(false); });
    return () => control.abort();
  }, [open?.kind, local, runtimePath]);
  const toggleEnabled = () => {
    const next = enabled.includes(mode) ? enabled.filter(item => item !== mode) : [...enabled, mode];
    setEnabled(next);
    if (!next.includes(defaultMode || "")) setDefaultMode(next[0] || null);
  };
  const chooseBackend = (kind: Backend["kind"], value: string) => {
    const backend = backends.find(item => item.id === value);
    onDraft(kind === "realtime" ? {realtime_backend: value, realtime_voice: backend?.voices[0] || ""} : {[`${kind}_backend`]: value});
  };
  const selected = open?.kind === "agent" ? undefined : backends.find(item => item.id === (open?.kind === "realtime" ? defaults.realtime_backend : open?.kind === "tts" ? defaults.tts_backend : defaults.stt_backend));
  const voice = open?.kind === "realtime" ? defaults.realtime_voice || "" : selected ? defaults.voices[selected.id] || selected.voices[0] || "" : "";
  const titles = {stt: "Speech To Text", tts: "Text To Speech", realtime: "Realtime Provider", agent: agentName};
  async function save() {
    setSaving(true); setRuntimeError(""); setOpen(null);
    let runtimeSaved = false;
    try {
      if (profileDraft !== null) {
        await jsonResponse(await ownerFetch(runtimePath, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({profile_id: profileDraft})}));
        runtimeSaved = true;
      }
      if (await onSave({enabled_modes: enabled, default_mode: enabled.length ? defaultMode : null})) {
        setProfileDraft(null);
      } else if (runtimeSaved) setRuntimeError("The agent model profile was saved, but voice settings were not. Your voice edits are retained; retry Save pipeline configuration.");
    } catch (err) { setRuntimeError(`Agent model profile was not saved. Voice edits are retained. ${err instanceof Error ? err.message : "Try again."}`); }
    finally { setSaving(false); }
  }
  return <div className="agent-pipeline-editor">
    <div className="pipeline-editor-toolbar"><div role="tablist" aria-label="Configure mode providers" className="pipeline-tabs">
      {policy.integration_modes.map((item, index, modes) => <button type="button" key={item} role="tab" id={`${id}-${item}`} aria-controls={`${id}-panel`} aria-selected={mode === item} tabIndex={mode === item ? 0 : -1} disabled={busy || saving} onClick={() => { setOpen(null); onModeChange(item); }} onKeyDown={event => {
        const next = event.key === "Home" ? 0 : event.key === "End" ? modes.length - 1 : event.key === "ArrowRight" ? (index + 1) % modes.length : event.key === "ArrowLeft" ? (index + modes.length - 1) % modes.length : -1;
        if (next >= 0) { event.preventDefault(); setOpen(null); onModeChange(modes[next]); document.getElementById(`${id}-${modes[next]}`)?.focus(); }
      }}>{item === "realtime" ? "Live / Realtime" : "Standard"}</button>)}
    </div><RefreshButton label="Discard pipeline edits and refresh" busy={busy || saving} onClick={() => { setProfileDraft(null); setOpen(null); onRefresh(); }}/></div>
    <fieldset disabled={busy || saving} className="pipeline-editor-content" role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${mode}`}>
      <AgentPipeline mode={mode} agentName={agentName} stt={defaults.stt_backend} tts={defaults.tts_backend} realtime={defaults.realtime_backend}
        enabled={enabled.includes(mode)} isDefault={defaultMode === mode} onToggleEnabled={toggleEnabled} onSetDefault={() => setDefaultMode(mode)}
        onEdit={(kind, anchor) => setOpen({kind, anchor})} onEditAgent={event => setOpen({kind: "agent", anchor: event.currentTarget})}/>
    </fieldset>
    {!enabled.length && <p role="status">Chat-only · No voice modes enabled.</p>}
    {runtimeError && <p role="alert">{runtimeError}</p>}
    {dirty ? <p role="status">Unsaved pipeline changes.</p> : saved && <p role="status">{saved}</p>}
    <footer className="pipeline-save"><button type="button" className="button-primary" disabled={busy || saving || !!enabled.length && !enabled.includes(defaultMode || "")} onClick={() => void save()}>{saving ? "Saving…" : "Save pipeline configuration"}</button></footer>
    {open && <EntityPopover anchor={open.anchor} title={`${titles[open.kind]} settings`} onClose={() => setOpen(null)}>
      {open.kind === "agent" ? !local ? <p>This agent’s model is managed by its enrolled integration. Model choices are configured there.</p> : <>
        {runtimeLoading && <p role="status">Loading model profiles…</p>}
        {runtimeError && <p role="alert">{runtimeError}</p>}
        {!runtimeLoading && runtime && <Choices label="Agent model profile" value={profileDraft ?? runtime.profile_id ?? ""} options={[
          ...(!runtime.profile_id ? [{value: "", label: runtime.model || "Current runtime", detail: "Keep current runtime configuration"}] : []),
          ...profiles.map(profile => ({value: profile.id, label: profile.name, detail: `${profile.provider} · ${profile.model || "Model configured in Settings"}${profile.provider === "openai" ? " · Unsupported for this agent" : ""}`, disabled: profile.provider !== "ollama"})),
        ]} onChange={value => setProfileDraft(value || null)}/>}
      </> : <>
        <Choices label={open.kind === "realtime" ? "Realtime provider / model" : `${open.kind.toUpperCase()} backend`} value={selected?.id || ""} options={backends.filter(item => item.kind === open.kind).map(item => ({value: item.id, label: item.id, detail: `${item.model} · ${item.readiness}`}))} onChange={value => chooseBackend(open.kind as Backend["kind"], value)}/>
        {(open.kind === "tts" || open.kind === "realtime") && <Choices label={open.kind === "tts" ? "Voice" : "Realtime voice"} value={voice} options={(selected?.voices || []).map(value => ({value, label: value}))} onChange={value => onDraft(open.kind === "realtime" ? {realtime_voice: value} : {voices: {[selected!.id]: value}})}/>}
        {open.kind === "tts" && preview}
        {open.kind === "realtime" && <p>Voice samples are available for Standard text-to-speech providers only.</p>}
      </>}
    </EntityPopover>}
  </div>;
}
