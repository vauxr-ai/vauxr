import { ownerFetch } from "../auth/api";
import SupportSettings from "./SupportSettings";
import IntegrationMetadata from "./IntegrationMetadata";
import AddAgent from "./AddAgent";
import { useEffect, useState, useRef, useId } from "react";
import { useAgents, type ApiAgent } from "../hooks/useAgents";
import Icon from "./Icon";
import Modal from "./Modal";
import RefreshButton from "./RefreshButton";
import AgentAvatar from "./AgentAvatar";
import SpeechSettings from "./SpeechSettings";

/** Selecting an action dismisses its menu; dialogs return to the visible trigger. */
function dismissActionMenu(button: HTMLButtonElement): HTMLElement {
  const menu = button.closest<HTMLDetailsElement>("details.agent-actions");
  if (!menu) return button;
  const actions = menu.querySelector<HTMLElement>(".agent-actions-list");
  if (actions) actions.inert = true;
  menu.open = false;
  return menu.querySelector<HTMLElement>("summary") || button;
}

/** Fixed viewport coordinates keep pointer hints near the cursor without clipping. */
function positionDeleteTooltip(trigger: HTMLElement, pointer?: {x: number; y: number}) {
  const tooltip = trigger.querySelector<HTMLElement>('[role="tooltip"]');
  if (!tooltip) return;
  const box = tooltip.getBoundingClientRect();
  const target = trigger.getBoundingClientRect();
  const margin = 8, offset = 12;
  let x = pointer ? pointer.x + offset : target.right - box.width;
  if (pointer && x + box.width > window.innerWidth - margin) x = pointer.x - box.width - offset;
  let y = pointer ? pointer.y - box.height / 2 : target.bottom + offset;
  if (!pointer && y + box.height > window.innerHeight - margin) y = target.top - box.height - offset;
  tooltip.style.setProperty('--tooltip-x', `${Math.max(margin, Math.min(x, window.innerWidth - box.width - margin))}px`);
  tooltip.style.setProperty('--tooltip-y', `${Math.max(margin, Math.min(y, window.innerHeight - box.height - margin))}px`);
}

export default function AgentsPanel({
  onContext,
  onSelection,
  browserDeviceId, browserConnected = false, browserCredential,
}: {
  browserDeviceId?: string; browserConnected?: boolean; browserCredential?: () => string | null;
  onContext?: (context: string) => void;
  onSelection?: (selection: { id: string; name: string } | null) => void;
}) {
  const api = useAgents();
  const runtimeTabsId = useId();
  const [runtimeTab, setRuntimeTab] = useState("configuration");
  const deleteReturnFocus = useRef<HTMLElement>(null);
  const [deleteId,setDeleteId]=useState("");
  const [deleteError,setDeleteError]=useState("");
  const [deleting,setDeleting]=useState(false);
  const voiceReturnFocus = useRef<HTMLElement>(null);
  const [voiceId, setVoiceId] = useState("");
  const avatarReturnFocus = useRef<HTMLButtonElement>(null);
  const diagnosticsReturnFocus = useRef<HTMLElement>(null);
  const [avatarOpen, setAvatarOpen] = useState(false);
  const [diagnosticsId, setDiagnosticsId] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [avatarRevision, setAvatarRevision] = useState(0);
  const [avatarError, setAvatarError] = useState("");
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [agents, setAgents] = useState<ApiAgent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [nameDraft, setNameDraft] = useState("");
  const [saved, setSaved] = useState("");
  const voiceAgent = agents.find(agent => agent.id === voiceId);
  const diagnostics = agents.find(agent => agent.id === diagnosticsId);
  const deletingAgent = agents.find(agent=>agent.id === deleteId);
  const selected = agents.find((agent) => agent.id === selectedId);
  useEffect(() => {
    onContext?.(selected?.name || "Agents");
  }, [selected?.name, onContext]);
  useEffect(() => {
    onSelection?.(selected ? { id: selected.id, name: selected.name } : null);
  }, [selected?.id, selected?.name, onSelection]);
  const refresh = async () => {
    try {
      setAgents(await api.listAgents());
      setError("");
    } catch {
      setError("Unable to load routing agents. Try refreshing.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void refresh();
    const update = () => {
      void refresh();
    };
    window.addEventListener("companion-resource-changed", update);
    const policyUpdate = (event: Event) => {
      if ((event as CustomEvent).detail?.agentId) update();
    };
    window.addEventListener("speech-settings-changed", policyUpdate);
    return () => {
      window.removeEventListener("companion-resource-changed", update);
      window.removeEventListener("speech-settings-changed", policyUpdate);
    };
  }, []);

  useEffect(() => {
    const closeMenus = (event: PointerEvent | KeyboardEvent) => {
      if (document.querySelector("dialog[open]")) return;
      const escape = event instanceof KeyboardEvent && event.key === "Escape";
      if (event instanceof KeyboardEvent && !escape) return;
      document.querySelectorAll<HTMLDetailsElement>(".agent-actions[open]").forEach(menu => {
        if (escape || !menu.contains(event.target as Node)) {
          // The close fade retains content briefly; remove its controls immediately.
          const actions = menu.querySelector<HTMLElement>(".agent-actions-list");
          if (actions) actions.inert = true;
          menu.open = false;
          if (escape) menu.querySelector<HTMLElement>("summary")?.focus();
        }
      });
    };
    document.addEventListener("pointerdown", closeMenus);
    document.addEventListener("keydown", closeMenus);
    return () => {document.removeEventListener("pointerdown", closeMenus); document.removeEventListener("keydown", closeMenus);};
  }, []);

  async function activate(agent: ApiAgent) {
    if (
      !window.confirm(
        `Make ${agent.name} the default fallback route for unassigned devices? This changes server routing, not just your companion.`,
      )
    )
      return;
    setBusy(true);
    try {
      await api.activateAgent(agent.id);
      await refresh();
      window.dispatchEvent(new Event("agent-route-changed"));
    } catch (error) {
      setError(
        error instanceof Error
          ? error.message
          : "Unable to set the default agent. Refresh before retrying.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function uploadAvatar(file?: File) {
    if (!file || !selected || avatarBusy) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 1024 * 1024) {
      setAvatarError("Choose a PNG, JPEG or WebP image up to 1 MB."); return;
    }
    setAvatarBusy(true); setAvatarError("");
    try {
      const response = await ownerFetch(`/api/agents/${encodeURIComponent(selected.id)}/avatar`, {
        method: "POST", headers: {"Content-Type": file.type}, body: file,
      });
      if (!response.ok) throw new Error();
      setAvatarRevision(value => value + 1); setAvatarOpen(false); setSaved("Agent avatar saved.");
    } catch {setAvatarError("Avatar upload failed. Try another image or retry.");}
    finally {setAvatarBusy(false);}
  }


  return (
    <div className="space-y-5">
      <div className="resource-toolbar agents-toolbar">
        <AddAgent
          onCreated={async (created) => {
            await refresh();
            setEditingName(false);
            setSelectedId("");
            setNameDraft(created.name);
          }}
        />
      </div>
      <section className="card agent-inventory" aria-label="Agents available for use on voice devices">
        <div className="section-heading">
          <div>
            <h2>Agents available for use on voice devices</h2>
            <p>
              The default agent serves devices that have no explicit agent
              assignment.
            </p>
          </div>
          <RefreshButton label="Refresh agents" busy={loading || busy} onClick={refresh} />
        </div>
        {error && !diagnosticsId && !voiceId && (
          <p role="alert" className="p-5 text-red-300">
            {error}
          </p>
        )}
        {loading ? (
          <p role="status" className="p-5">
            Loading agents…
          </p>
        ) : agents.length === 0 ? (
          <div className="empty-state">
            <Icon name="agents" size={32} />
            <h3>No routing agents yet</h3>
            <p>
              Add a registered agent, or connect an external agent in Settings →
              Apps & Integrations.
            </p>
          </div>
        ) : (
          <ul className="agent-list">
            {agents.map((agent) => (
              <li key={agent.id} data-agent-id={agent.id}>
                <button className="agent-avatar-edit" aria-label={`Edit avatar for ${agent.name}`} title="Edit agent avatar" onClick={event => {
                  (avatarReturnFocus as {current:HTMLButtonElement|null}).current=event.currentTarget;
                  setSelectedId(agent.id); setAvatarError(""); setAvatarOpen(true);
                }}><AgentAvatar agentId={agent.id} revision={avatarRevision}/><span className="avatar-edit-badge" aria-hidden="true">✎</span></button>
                <div className="agent-name">
                  <div className="agent-inline-title">
                    {editingName && selectedId === agent.id ? <form id="rename-agent" onSubmit={async event => {
            event.preventDefault(); if (!selected) return; setBusy(true); setError(""); setSaved("");
            try {const renamed = await api.renameAgent(selected.id, nameDraft.trim());
              setAgents(current => current.map(agent => agent.id === renamed.id ? {...agent, name:renamed.name} : agent));
              setNameDraft(renamed.name); setEditingName(false); setSaved("Agent name saved.");
              window.dispatchEvent(new Event("agent-route-changed"));
            } catch (failure) {setError(failure instanceof Error ? failure.message : "Unable to save the agent name.");}
            finally {setBusy(false);}
          }}><label className="sr-only" htmlFor="agent-name-edit">Agent name</label>
            <input id="agent-name-edit" value={nameDraft} maxLength={120} required disabled={busy} autoFocus
              onChange={event => setNameDraft(event.target.value)} />
            <div className="name-edit-actions"><button type="submit" disabled={busy || !nameDraft.trim()}>Save name</button>
            <button type="button" disabled={busy} onClick={() => {setNameDraft(selected?.name || ""); setEditingName(false); setError("");}}>Cancel name edit</button></div>
          </form> : <h3 aria-label={agent.name}>{agent.name}<button type="button" aria-label="Edit agent name" title="Edit agent name"
            onClick={() => {setSelectedId(agent.id); setNameDraft(agent.name); setEditingName(true); setSaved(""); setError("");}}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L5 15Z" /></svg></button></h3>}
                  </div>
                  <p>
                    {agent.type === "langchain"
                      ? "LangChain · registered"
                      : agent.type === "langgraph"
                        ? "Standalone agent"
                        : agent.type === "support"
                          ? "Built-in · independent runtime"
                          : agent.type === "openclaw-direct"
                            ? "OpenClaw direct"
                            : "OpenClaw connection"}
                  </p>
                </div>
                <span className="pill">
                  {agent.chat_only
                    ? "Chat-only · no voice"
                    : agent.active
                      ? "Default"
                      : "Not default"}
                </span>
                {!agent.active && <button
                  className="agent-route-action"
                  disabled={busy}
                  onClick={() => void activate(agent)}
                >
                  Set as default
                </button>}
                <details className="agent-actions" onToggle={event => {
                  const actions = event.currentTarget.querySelector<HTMLElement>(".agent-actions-list");
                  if (actions) {
                    actions.inert = !event.currentTarget.open;
                    if (event.currentTarget.open) {
                      const trigger = event.currentTarget.querySelector("summary")!.getBoundingClientRect();
                      event.currentTarget.dataset.placement = trigger.bottom + 6 + actions.getBoundingClientRect().height > window.innerHeight - 16 ? "above" : "below";
                    }
                  }
                }}><summary aria-label={`Actions for ${agent.name}`} onClick={event => {
                  const menu = event.currentTarget.parentElement as HTMLDetailsElement;
                  const actions = menu.querySelector<HTMLElement>(".agent-actions-list");
                  if (actions) actions.inert = menu.open;
                }}>⋯</summary><div className="agent-actions-list">
                <button className="agent-route-action" aria-label={`Voice pipeline for ${agent.name}`}
                  onClick={event => {(voiceReturnFocus as {current: HTMLElement | null}).current = dismissActionMenu(event.currentTarget); setVoiceId(agent.id);}}><Icon name="agents" size={20}/>Voice pipeline</button>
                <button className="agent-route-action" aria-label={`Runtime settings for ${agent.name}`} title="Agent runtime settings"
                  onClick={event => { (diagnosticsReturnFocus as {current: HTMLElement | null}).current = dismissActionMenu(event.currentTarget); setError(""); setDiagnosticsId(agent.id); }}><Icon name="settings" size={20}/>Runtime settings</button>
                <span className="agent-action-tooltip-trigger"
                  tabIndex={agent.active || agent.builtin ? 0 : undefined}
                  aria-label={agent.active || agent.builtin ? `Delete ${agent.name} unavailable` : undefined}
                  aria-describedby={agent.active || agent.builtin ? `delete-reason-${agent.id}` : undefined}
                  onPointerEnter={event => { if (event.pointerType !== "touch") positionDeleteTooltip(event.currentTarget, {x:event.clientX,y:event.clientY}); }}
                  onPointerMove={event => { if (event.pointerType !== "touch") positionDeleteTooltip(event.currentTarget, {x:event.clientX,y:event.clientY}); }}
                  onFocus={event => { if (event.currentTarget.matches(":focus-visible")) positionDeleteTooltip(event.currentTarget); }}
                  onPointerDown={event => { if (agent.active || agent.builtin) {
                    event.currentTarget.focus();
                    positionDeleteTooltip(event.currentTarget, event.pointerType === "touch" ? undefined : {x:event.clientX,y:event.clientY});
                  } }}>
                  <button type="button" className="agent-delete-action" disabled={agent.active || agent.builtin || busy} onClick={event => {
                    (deleteReturnFocus as {current:HTMLElement|null}).current=dismissActionMenu(event.currentTarget); setDeleteId(agent.id); setDeleteError("");
                  }}><Icon name="trash" size={20}/>Delete agent</button>
                  {(agent.active || agent.builtin) && <span role="tooltip" className="agent-action-tooltip" id={`delete-reason-${agent.id}`}>
                    {agent.active ? "Set another agent as default first" : "Bundled agent cannot be deleted"}
                  </span>}
                </span>
                </div></details>
              </li>
            ))}
          </ul>
        )}
      </section>
      {saved && <p role="status">{saved}</p>}
      {deletingAgent && <Modal title="Delete agent" returnFocus={deleteReturnFocus} onClose={()=>{if(!deleting)setDeleteId("");}}>
        <p>Delete <strong>{deletingAgent.name}</strong>? Assigned devices will use the default agent.</p>
        {deleteError && <p role="alert">{deleteError}</p>}
        {deleting && <p role="status">Deleting agent…</p>}
        <button type="button" disabled={deleting} onClick={()=>setDeleteId("")}>Cancel</button>
        <button type="button" className="button-primary" disabled={deleting} onClick={async()=>{
          setDeleting(true);setDeleteError("");
          try { await api.deleteAgent(deletingAgent.id); await refresh(); setDeleteId("");setSelectedId("");setDiagnosticsId(""); window.dispatchEvent(new Event("agent-route-changed")); document.querySelector<HTMLButtonElement>('.agent-create-action > button')?.focus(); }
          catch(err){setDeleteError(err instanceof Error?err.message:"Unable to delete agent. Refresh and retry.");}
          finally {setDeleting(false);}
        }}>{deleting?"Deleting…":"Delete agent"}</button>
      </Modal>}
      {voiceAgent && <Modal title="Voice pipeline" className="agent-detail-modal agent-detail agent-voice-modal"
        returnFocus={voiceReturnFocus} onClose={() => setVoiceId("")}>
        <h3>{voiceAgent.name}</h3>
        <SpeechSettings onSaved={() => setVoiceId("")} key={`voice-${voiceAgent.id}`} agentId={voiceAgent.id} agentName={voiceAgent.name} agentType={voiceAgent.type}
          browserDeviceId={browserDeviceId} browserConnected={browserConnected} browserCredential={browserCredential} />
      </Modal>}
      {avatarOpen && selected && <Modal title="Edit agent avatar" className="avatar-editor-modal" returnFocus={avatarReturnFocus}
        onClose={() => {if (!avatarBusy) setAvatarOpen(false);}}>
        <p>Choose a PNG, JPEG or WebP image up to 1 MB.</p>
        <label className="avatar-dropzone" onDragOver={event => event.preventDefault()} onDrop={event => {
          event.preventDefault(); if (!avatarBusy) void uploadAvatar(event.dataTransfer.files[0]);
        }}>
          <AgentAvatar agentId={selected.id} revision={avatarRevision} />
          <strong>{avatarBusy ? "Uploading…" : "Drop an image here or choose a file"}</strong>
          <input type="file" aria-label="Choose agent avatar" accept="image/png,image/jpeg,image/webp" disabled={avatarBusy}
            onChange={event => {const file = event.target.files?.[0]; event.target.value = ""; if (file) void uploadAvatar(file);}} />
        </label>
        {avatarError && <p role="alert">{avatarError}</p>}
        <button disabled={avatarBusy} onClick={() => setAvatarOpen(false)}>Cancel</button>
      </Modal>}
      {diagnostics && <Modal title="Agent runtime settings" className="agent-detail-modal agent-detail agent-diagnostics-modal"
        returnFocus={diagnosticsReturnFocus} onClose={() => setDiagnosticsId("")}>
        <h3>{diagnostics.name}</h3>
        <button disabled={diagnostics.active || busy} onClick={() => void activate(diagnostics)}>
          {diagnostics.active ? "Default" : "Set as default"}
        </button>
        {error && <p role="alert">{error}</p>}
        <div className="runtime-tabs" role="tablist" aria-label="Agent runtime sections">
          {["configuration", "diagnostics"].map((tab,index) => <button type="button" role="tab" key={tab}
            id={`${runtimeTabsId}-${tab}`} aria-controls={`${runtimeTabsId}-${tab}-panel`} aria-selected={runtimeTab === tab} tabIndex={runtimeTab === tab ? 0 : -1}
            onClick={() => setRuntimeTab(tab)} onKeyDown={event => {
              if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
              event.preventDefault(); const next = event.key === "Home" ? "configuration" : event.key === "End" ? "diagnostics" : index ? "configuration" : "diagnostics";
              setRuntimeTab(next); document.getElementById(`${runtimeTabsId}-${next}`)?.focus();
            }}>{tab === "configuration" ? "Configuration" : "Diagnostics"}</button>)}
        </div>
        <section className="runtime-panel" role="tabpanel" id={`${runtimeTabsId}-configuration-panel`} aria-labelledby={`${runtimeTabsId}-configuration`} hidden={runtimeTab !== "configuration"}>
        {diagnostics.type === "support" ? <SupportSettings /> : diagnostics.type === "langchain" ? <SupportSettings agentId={diagnostics.id} /> : null}
          {!["support","langchain"].includes(diagnostics.type) && <p>Runtime configuration is managed by this agent’s integration. See Diagnostics for its configuration source and connection guidance.</p>}
        </section>
        <section className="runtime-panel" role="tabpanel" id={`${runtimeTabsId}-diagnostics-panel`} aria-labelledby={`${runtimeTabsId}-diagnostics`} hidden={runtimeTab !== "diagnostics"}>
        <p>Provider changes apply to new turns. Reconnect WebRTC after changing providers. Readiness checks availability, not model inference; unavailable providers do not fall back automatically.</p>
        <p>
          {diagnostics.type === "langchain"
            ? "LangChain · registered"
            : diagnostics.type === "langgraph"
              ? "Standalone agent"
              : diagnostics.type === "support"
                ? "Built-in · independent runtime"
                : diagnostics.type === "openclaw-direct"
                  ? "OpenClaw direct"
                  : "OpenClaw connection"}{" "}
          · {diagnostics.active ? "Default" : "Not default"}
        </p>
        <p>
          This route is shared by devices. Changing its display name does
          not change routing or access.
        </p>
        <p>
          Agent ID: <code>{diagnostics.id}</code>
        </p>
        <p>
          {diagnostics.active
            ? "Fallback for unassigned devices only. Explicit device assignments and your independent companion selection are unchanged."
            : "Not the shared default. Explicit device assignments remain separate."}{" "}
          Setting the default does not prove the integration is online or
          ready; check the agent’s voice pipeline and device diagnostics.
        </p>
        <IntegrationMetadata metadata={diagnostics.integration_metadata} />
        </section>
      </Modal>}
      <p className="text-sm text-zinc-400">
        Connect external agents in Settings → Apps &amp; Integrations.
      </p>
    </div>
  );
}
