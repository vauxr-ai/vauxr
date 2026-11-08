import { useEffect, useRef, useState } from "react";
import { ownerFetch, jsonResponse, randomId } from "../auth/api";
import { useAgents, type ApiAgent } from "../hooks/useAgents";

export interface Selection { id: string; name: string }
interface Props {
  browserId: string;
  credential: () => string;
  epoch: number;
  route: string;
  selection: Selection | null;
  onSwitch: () => void;
}

export default function CompanionControls({ browserId, credential, epoch, route, selection, onSwitch }: Props) {
  const { listAgents } = useAgents();
  const [agents, setAgents] = useState<ApiAgent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [revision, setRevision] = useState("");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [text, setText] = useState("");
  const [reply, setReply] = useState("");
  const [chatError, setChatError] = useState("");
  const generation = useRef(0);
  const queue = useRef(Promise.resolve());
  const [refresh, setRefresh] = useState(0);
  const request = async (action: string, body: object) => jsonResponse(await ownerFetch(`/api/companion/${action}`, {
    method: "POST", headers: { "X-Vauxr-Browser": credential() },
    body: JSON.stringify({ browser_id: browserId, ...body }),
  }));

  useEffect(() => {
    let alive = true;
    void listAgents().then(rows => { if (alive) setAgents(rows); }).catch(() => {});
    if (browserId) void ownerFetch(`/api/devices/${encodeURIComponent(browserId)}/speech`)
      .then(jsonResponse).then(data => { if (alive) setAgentId(data.policy?.agent_id || ""); }).catch(() => {});
    return () => { alive = false; };
  }, [browserId, epoch, listAgents, refresh]);

  useEffect(() => {
    const attempt = ++generation.current;
    setRevision(""); setError(""); setDraft("");
    if (!browserId) return;
    // Serialize navigation updates, including reconnects; an older request cannot
    // overwrite a later selection just because its response arrived last.
    queue.current = queue.current.catch(() => {}).then(async () => {
      if (generation.current !== attempt) return;
      const context = await request("context", {
        route, resource_type: selection ? "agent" : null,
        resource_id: selection?.id ?? null, setting_key: selection ? "name" : null,
        current_value: selection?.name ?? null,
      });
      if (generation.current === attempt) setRevision(context.revision);
    }).catch(() => { if (generation.current === attempt) setError("Context unavailable. Refresh the selected resource."); });
    return () => { generation.current++; };
  }, [browserId, epoch, route, selection?.id, selection?.name, refresh]);

  const run = async (work: () => Promise<void>, failure = "Change unavailable or stale. Refresh the resource and try again.") => {
    const attempt = generation.current;
    setBusy(true); setError("");
    try { await work(); }
    catch { if (attempt === generation.current) setError(failure); }
    finally { setBusy(false); }
  };
  return <div className="companion-controls auth-panel space-y-3">
    <label>Conversation agent
      <select aria-describedby="companion-selected-agent" aria-label="Conversation agent" value={agentId} disabled={!browserId || busy || chatBusy}
        onChange={event => { const value = event.target.value; void run(async () => {
          generation.current++; setRevision("");
          setReply(""); setChatError("");
          onSwitch();
          await request("select", { agent_id: value }); setAgentId(value); setRefresh(n => n + 1);
          window.dispatchEvent(new Event("agent-route-changed"));
        }, "Unable to switch. Resolve browser voice override conflicts in Devices, then retry."); }}>
        <option value="" disabled>{browserId ? "Choose agent" : "Connect this browser first"}</option>
        {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}{agent.chat_only ? " · Chat-only" : ""}</option>)}
      </select>
    </label>
    <p id="companion-selected-agent" className="text-xs">{agents.find(agent => agent.id === agentId)?.name || "Select a conversation agent for this browser."}</p>
    <form className="companion-text" onSubmit={event => {
      event.preventDefault();
      if (!browserId || !text.trim() || busy || chatBusy) return;
      const message = text.trim();
      const attempt = generation.current;
      setChatBusy(true); setChatError(""); setReply("");
      void request("chat", { request_id: randomId(), text: message }).then(result => {
        if (attempt !== generation.current) return;
        setReply(result.text); setText("");
        setRefresh(n => n + 1);
        window.dispatchEvent(new Event("companion-resource-changed"));
        window.dispatchEvent(new Event("agent-route-changed"));
      }).catch(() => setChatError("Text request failed or its outcome is uncertain. Check action status before sending again."))
        .finally(() => setChatBusy(false));
    }}>
      <label>Text message
        <textarea aria-label="Text message" maxLength={2000} rows={2}
          value={text} disabled={!browserId || chatBusy} onChange={event => setText(event.target.value)} />
      </label>
      <button disabled={!browserId || !text.trim() || busy || chatBusy}>{chatBusy ? "Waiting for agent…" : "Send text"}</button>
      {chatError && <p role="alert">{chatError}</p>}
      {reply && <p className="companion-reply" role="status" aria-label="Agent text response">{reply}</p>}
    </form>
    {selection && <div className="companion-setting">
      <p>Selected agent: {selection.name} · Display name</p>
      <label>Display name
        <input aria-label="Display name" maxLength={120} value={draft}
          disabled={!revision || busy || chatBusy} onChange={event => setDraft(event.target.value)} />
      </label>
      <button disabled={!revision || !draft.trim() || busy || chatBusy} onClick={() => void run(async () => {
        await jsonResponse(await ownerFetch(`/api/agents/${selection.id}`, {
          method: "PATCH", body: JSON.stringify({ name: draft }),
        }));
        setRevision(""); setRefresh(n => n + 1);
        window.dispatchEvent(new Event("companion-resource-changed"));
        window.dispatchEvent(new Event("agent-route-changed"));
      })}>Save display name</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
