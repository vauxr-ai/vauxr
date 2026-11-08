import RefreshButton from "./RefreshButton";
import AccessPanel from "./AccessPanel";
import EventLog from "./EventLog";
import DeviceVoicePipeline from "./DeviceVoicePipeline";
import DeviceMetrics from "./DeviceMetrics";
import Modal from "./Modal";
import "./DevicesPanel.css";
import { ownerFetch } from "../auth/api";
import { useCallback, useEffect, useRef, useState } from "react";
import { deriveHttpUrl, useHttpApi } from "../hooks/useHttpApi";
import { type ApiWebhook, useWebhooks } from "../hooks/useWebhooks";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import Icon from "./Icon";

type FollowUpMode = "auto" | "always" | "never";
type GestureId = "double_press" | "triple_press" | "long_press";
type ActionKind = "none" | "prompt" | "announce" | "command" | "webhook";

interface ButtonAction {
  kind: ActionKind;
  text?: string;
  command?: string;
  volume?: number;
  webhook_id?: string;
}

interface DeviceConfig {
  name?: string;
  pipeline_mode?: "standard" | "realtime" | null;
  transport_mode?: "websocket" | "webrtc";
  voice_mode?: "websocket" | "webrtc" | "realtime" | null;
  follow_up_mode?: FollowUpMode;
  output_sample_rate?: number | null;
  barge_in?: boolean;
  button_actions?: Partial<Record<GestureId, ButtonAction>>;
}

interface ApiDeviceWithConfig {
  chat_only?: boolean;
  id: string;
  name: string;
  state: string;
  lastSeen: string;
  config: DeviceConfig;
  platform?: string;
  fw_version?: string;
}

const STATE_PILL: Record<string, string> = {
  idle: "border-zinc-700/50 bg-zinc-800/50 text-zinc-400",
  listening: "border-red-500/30 bg-red-500/10 text-red-300",
  processing: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  speaking: "border-violet-500/30 bg-violet-500/10 text-violet-300",
  offline: "border-zinc-700/50 bg-zinc-800/30 text-zinc-500",
};

const STATE_DOT: Record<string, string> = {
  idle: "bg-zinc-500",
  listening: "bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.6)]",
  processing: "bg-amber-400 shadow-[0_0_8px_rgba(245,158,11,0.6)]",
  speaking: "bg-violet-400 shadow-[0_0_8px_rgba(124,58,237,0.6)]",
  offline: "bg-zinc-700",
};

const FOLLOW_UP_OPTIONS: FollowUpMode[] = ["auto", "always", "never"];
const DEFAULT_ANNOUNCEMENT = "Hello world! this is the vauxr speaker test announcement. I sure hope you can hear me";
const GESTURE_ROWS: { id: GestureId; label: string }[] = [
  { id: "double_press", label: "Double press" },
  { id: "triple_press", label: "Triple press" },
  { id: "long_press", label: "Long press" },
];
const ACTION_KINDS: { id: ActionKind; label: string }[] = [
  { id: "none", label: "none" },
  { id: "prompt", label: "prompt" },
  { id: "announce", label: "announce" },
  { id: "command", label: "command" },
  { id: "webhook", label: "webhook" },
];
const BUTTON_COMMANDS = ["mute", "unmute", "reboot", "set_volume"] as const;

interface SaveStatus {
  status: "saving" | "saved" | "error";
  message?: string;
}

interface Props {
  logs?: LogEntry[];
  onContext?: (context: string) => void;
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  addLog: (dir: LogEntry["dir"], text: string, deviceId?: string) => void;
}

const POLL_INTERVAL_MS = 5_000;

const inputClass =
  "rounded-md border border-white/5 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/30 disabled:opacity-60";

const labelClass = "flex flex-col gap-1.5 text-xs font-medium text-zinc-400";

function formatLastSeen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString();
}

export default function DevicesPanel({
  wsUrl,
  token,
  wsState,
  addLog,
  onContext,
  logs = [],
}: Props) {
  // Device administration is owner-session based, not contingent on a voice socket.
  const baseUrl = wsUrl ? deriveHttpUrl(wsUrl) : window.location.origin;
  const api = useHttpApi(baseUrl, token);
  const { listWebhooks } = useWebhooks(baseUrl, token);

  const [addingDevice,setAddingDevice] = useState(false);
  const [devices, setDevices] = useState<ApiDeviceWithConfig[]>([]);
  const [webhooks, setWebhooks] = useState<ApiWebhook[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<Record<string, SaveStatus>>({});
  const pendingWrites = useRef(0);
  const writeQueue = useRef(Promise.resolve());
  const revision = useRef(0);
  const deviceWriteVersion = useRef<Record<string, number>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    if (pendingWrites.current) return;
    const readRevision = revision.current;
    try {
      const res = await ownerFetch("/api/devices");
      if (!res.ok) {
        let msg = res.statusText;
        try {
          const body = await res.json();
          if (body.error) msg = body.error;
        } catch {
          /* fall through */
        }
        throw new Error(msg);
      }
      const body = await res.json();
      const list: ApiDeviceWithConfig[] = Array.isArray(body)
        ? body
        : (body.devices ?? []);
      if (readRevision !== revision.current || pendingWrites.current) return;
      setDevices(list);
      try {
        setWebhooks(await listWebhooks());
      } catch {
        // Webhooks are optional for the devices list; gesture editors degrade.
      }
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [listWebhooks]);

  useEffect(() => {
    if (wsState === "disconnected") return;
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [wsState, refresh]);

  const updateDeviceConfig = useCallback(
    async (deviceId: string, patch: Partial<DeviceConfig>, label: string) => {
      ++pendingWrites.current;
      ++revision.current;
      const ticket = (deviceWriteVersion.current[deviceId] || 0) + 1;
      deviceWriteVersion.current[deviceId] = ticket;
      setSaveStatus((s) => ({ ...s, [deviceId]: { status: "saving" } }));
      const prior = writeQueue.current;
      let release!: () => void;
      writeQueue.current = new Promise<void>(resolve => { release = resolve; });
      await prior;
      try {
        const res = await ownerFetch(
          `/api/devices/${encodeURIComponent(deviceId)}`,
          {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(patch),
          },
        );
        if (!res.ok) {
          let msg = res.statusText;
          try {
            const body = await res.json();
            if (body.error) msg = body.error;
          } catch {
            /* fall through */
          }
          throw new Error(msg);
        }
        const updated = (await res.json()) as ApiDeviceWithConfig;
        setDevices((list) =>
          list.map((d) => {
            if (d.id !== deviceId) return d;
            const config = { ...d.config, ...(updated.config ?? {}) };
            if (
              updated.config &&
              !("button_actions" in updated.config) &&
              d.config?.button_actions
            ) {
              config.button_actions = d.config.button_actions;
            }
            return { ...d, ...updated, config };
          }),
        );
        if (deviceWriteVersion.current[deviceId] === ticket) setSaveStatus((s) => ({ ...s, [deviceId]: { status: "saved" } }));
        addLog("sys", `Device ${deviceId}: ${label}`, deviceId);

      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (deviceWriteVersion.current[deviceId] === ticket) setSaveStatus((s) => ({
          ...s,
          [deviceId]: { status: "error", message: msg },
        }));
        addLog("sys", `Device ${deviceId} update error: ${msg}`, deviceId);
      } finally {
        --pendingWrites.current;
        ++revision.current;
        release();
      }
    },
    [addLog],
  );

  const toggleExpanded = useCallback((id: string) => {
    setExpanded((e) => ({ [id]: !e[id] }));
  }, []);

  useEffect(() => {
    const selected = devices.find((device) => expanded[device.id]);
    onContext?.(
      selected
        ? selected.config?.name || selected.name || selected.id
        : "Devices",
    );
  }, [devices, expanded, onContext]);

  if (wsState === "disconnected") {
    return (
      <div className="card p-6 text-sm text-zinc-500">
        Connect to a server to manage devices.
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="p-4"><button onClick={()=>setAddingDevice(!addingDevice)} aria-expanded={addingDevice}>{addingDevice ? "Close Add Device" : "+ Add Device"}</button></div>
      {addingDevice && <AccessPanel mode="pairing"/>}
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">Devices</h2>
          <p className="text-xs text-zinc-500">
            Connected hardware, per-device settings, and actions.
          </p>
        </div>
        <RefreshButton label="Refresh" onClick={refresh} />
      </div>

      <div className="px-5 py-4">
        {error && (
          <p
            role="alert"
            className="mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300"
          >
            {error}
          </p>
        )}
        {loading ? (
          <p role="status" className="text-sm text-zinc-400">
            Loading devices…
          </p>
        ) : devices.length === 0 && !error ? (
          <p className="text-xs text-zinc-500">No devices yet</p>
        ) : (
          <ul className="space-y-2">
            {devices.map((d) => (
              <DeviceCard
                key={d.id}
                device={d}
                expanded={!!expanded[d.id]}
                onToggle={() => toggleExpanded(d.id)}
                onPatch={updateDeviceConfig}
                saveStatus={saveStatus[d.id]}
                api={api}
                addLog={addLog}
                httpBase={baseUrl}
                logs={logs}
                webhooks={webhooks}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface DeviceCardProps {
  logs: LogEntry[];
  device: ApiDeviceWithConfig;
  expanded: boolean;
  onToggle: () => void;
  onPatch: (id: string, patch: Partial<DeviceConfig>, label: string) => void;
  saveStatus?: SaveStatus;
  api: ReturnType<typeof useHttpApi>;
  addLog: (dir: LogEntry["dir"], text: string, deviceId?: string) => void;
  httpBase: string;
  webhooks: ApiWebhook[];
}

function DeviceCard({
  logs,
  device,
  expanded,
  onToggle,
  onPatch,
  saveStatus,
  api,
  addLog,
  httpBase,
  webhooks,
}: DeviceCardProps) {
  const [effectiveMode, setEffectiveMode] = useState<"standard" | "realtime" | null | "unknown">("unknown");
  const chatOnly = effectiveMode === null || (effectiveMode === "unknown" && device.chat_only === true);
  const pill = STATE_PILL[device.state] ?? STATE_PILL.offline;
  const dot = STATE_DOT[device.state] ?? STATE_DOT.offline;
  const mode: FollowUpMode = device.config?.follow_up_mode ?? "auto";
  const voiceMode = device.config?.voice_mode ?? "webrtc";
  const bargeIn = device.config?.barge_in !== false;
  const sampleRate = device.config?.output_sample_rate;

  const [nameDraft, setNameDraft] = useState(
    device.config?.name ?? device.name ?? "",
  );
  // Keep nameDraft in sync if the canonical name changes server-side and
  // we're not actively editing.
  const lastServerNameRef = useRef(device.config?.name ?? device.name ?? "");
  useEffect(() => {
    const serverName = device.config?.name ?? device.name ?? "";
    if (serverName !== lastServerNameRef.current) {
      lastServerNameRef.current = serverName;
      setNameDraft(serverName);
    }
  }, [device.config?.name, device.name]);

  const cancelName = useRef(false);
  const [editingName, setEditingName] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const diagnosticsButton = useRef<HTMLButtonElement>(null);
  const nameButton = useRef<HTMLButtonElement>(null);
  const [annText, setAnnText] = useState(DEFAULT_ANNOUNCEMENT);
  const [announcementBusy, setAnnouncementBusy] = useState(false);
  const [commandStatus, setCommandStatus] = useState("");
  const [commandBusy, setCommandBusy] = useState(false);
  const [annError, setAnnError] = useState("");

  const [ctlVolume, setCtlVolume] = useState("50");
  const [ctlOtaUrl, setCtlOtaUrl] = useState("");
  const [ctlBargeIn, setCtlBargeIn] = useState("true");
  const [ctlError, setCtlError] = useState("");
  const otaPlaceholder = `${httpBase}/firmware/${device.platform || "satellite1"}.bin`;

  const handleAnnounce = async () => {
    setAnnError("");
    const text = annText.trim() || DEFAULT_ANNOUNCEMENT;
    setAnnouncementBusy(true);
    try {
      await api.announce(device.id, text);
      addLog("sys", `Announce sent to ${device.id}: "${text}"`, device.id);
      setCommandStatus("Announcement sent");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setAnnError(msg);
      addLog("sys", `Announce error: ${msg}`, device.id);
    } finally { setAnnouncementBusy(false); }
  };

  const handleCommand = async (ctlCommand: string) => {
    if (commandBusy) return;
    setCtlError("");
    setCommandStatus("");
    if (ctlCommand === "set_volume" && (!Number.isFinite(Number(ctlVolume)) || Number(ctlVolume) < 0 || Number(ctlVolume) > 100)) { setCtlError("Volume must be between 0 and 100."); return; }
    setCommandBusy(true);
    try {
      const params =
        ctlCommand === "set_volume"
          ? { volume: Number(ctlVolume) }
          : ctlCommand === "ota"
            ? { url: ctlOtaUrl.trim() || otaPlaceholder }
            : ctlCommand === "set_barge_in"
              ? { enabled: ctlBargeIn === "true" }
              : undefined;
      await api.command(device.id, ctlCommand, params);
      addLog("sys", `Command ${ctlCommand} sent to ${device.id}`, device.id);
      setCommandStatus(`${ctlCommand.replace(/_/g, " ")} command sent`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setCtlError(msg);
      addLog("sys", `Command error: ${msg}`, device.id);
    } finally { setCommandBusy(false); }
  };

  const handleNameBlur = () => {
    if (cancelName.current) { cancelName.current = false; return; }
    const trimmed = nameDraft.trim();
    const current = device.config?.name ?? device.name ?? "";
    if (trimmed === current) return;
    onPatch(device.id, { name: trimmed }, `name → ${trimmed}`);
  };

  const saving = saveStatus?.status === "saving";
  const panelId = `device-card-${device.id}`;

  return (
    <li className="overflow-hidden rounded-lg border border-white/5 bg-zinc-900/40">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={panelId}
        className="focus-ring flex w-full items-center gap-3 px-3 py-3 text-left hover:bg-white/5"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-zinc-800/80 text-zinc-400">
          <Icon name="devices" size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium text-zinc-200">
              {device.config?.name || device.name || device.id}
            </span>
            <span className={`pill ${pill}`}>
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${dot}`} />
              {device.state}
            </span>
          </div>
          <div className="text-[11px] text-zinc-500">
            {device.id}
            {device.platform ? ` · ${device.platform}` : ""}
            {device.fw_version ? ` · ${device.fw_version}` : ""}
            {" · "}last seen {formatLastSeen(device.lastSeen)}
          </div>
        </div>
        <SaveBadge status={saveStatus} />
        <span
          aria-hidden
          className={`text-zinc-500 transition-transform ${expanded ? "rotate-90" : ""}`}
        >
          <Icon name="chevron-right" size={16} />
        </span>
      </button>

      {expanded && (
        <div
          id={panelId}
          className="space-y-4 border-t border-white/5 px-3 py-3"
        >
          <header className="device-detail-heading">
            {editingName ? <form onSubmit={event => { event.preventDefault(); handleNameBlur(); setEditingName(false); nameButton.current?.focus(); }}>
              <input aria-label="Device name" autoFocus value={nameDraft} onChange={event => setNameDraft(event.target.value)} onBlur={() => { handleNameBlur(); setEditingName(false); }} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); cancelName.current = true; setNameDraft(device.config?.name || device.name); setEditingName(false); nameButton.current?.focus(); } }} />
            </form> : <h3>{device.config?.name || device.name || device.id}<button ref={nameButton} type="button" className="device-pencil" aria-label="Edit device name" disabled={saving} onClick={() => { cancelName.current = false; setEditingName(true); }}><svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="m15 4 5 5M4 20l5-1L21 7a2 2 0 0 0-5-5L4 14Z"/></svg></button></h3>}
            <SaveBadge status={saveStatus}/>
          </header>
          <section className="device-section device-audio" aria-label="Speaker controls">
            <div className="device-volume">
              <label htmlFor={`${panelId}-volume`}>Output volume <span>{ctlVolume}%</span></label>
              <input id={`${panelId}-volume`} aria-label="Output volume" type="range" min="0" max="100" step="1" className="vauxr-volume-range" value={ctlVolume} disabled={commandBusy || chatOnly} onChange={event => setCtlVolume(event.target.value)} onPointerUp={() => void handleCommand("set_volume")} onKeyUp={event => { if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(event.key)) void handleCommand("set_volume"); }} />
              <small className="device-muted">Sends on release. Device volume readback is unavailable.</small>
            </div>
            <div className="device-announcement">
              <label className="device-field">Announcement message<input value={annText} onChange={event => setAnnText(event.target.value)} /></label>
              <button className="button-primary" type="button" disabled={announcementBusy || chatOnly} onClick={() => void handleAnnounce()}><Icon name="volume" size={16}/> {announcementBusy ? "Sending…" : "Test Announce"}</button>
            </div>
            {annError && <p role="alert">{annError}</p>}
            {commandStatus && <p role="status">{commandStatus}</p>}
            {ctlError && !diagnosticsOpen && <p role="alert">{ctlError}</p>}
          </section>
          <DeviceVoicePipeline deviceId={device.id} onModeChange={setEffectiveMode} transport={<label className="device-field">Voice transport<select aria-label="Voice transport" value={voiceMode === "websocket" ? "websocket" : "webrtc"} disabled={saving || chatOnly} onChange={event => onPatch(device.id, {transport_mode:event.target.value as "websocket" | "webrtc"}, `Voice transport → ${event.target.value}`)}><option value="websocket">WebSocket only · no barge-in</option><option value="webrtc">WebRTC · Standard or Live</option></select></label>} />
          <section aria-label="Device features" className="device-section space-y-3">
            <h3>Device features</h3>
            <fieldset disabled={saving || chatOnly} className="device-follow-up"><legend>Follow-up</legend><div role="group" aria-label="Follow-up mode">{FOLLOW_UP_OPTIONS.map(value => <button key={value} type="button" aria-pressed={mode === value} onClick={() => onPatch(device.id, {follow_up_mode:value}, `follow_up_mode → ${value}`)}>{value === "auto" ? "Auto" : value === "always" ? "Always" : "Never"}</button>)}</div></fieldset>
            {voiceMode === "webrtc" && effectiveMode !== "realtime" && <label className="device-switch"><input type="checkbox" role="switch" aria-label="Barge-in" checked={bargeIn} disabled={saving || chatOnly} onChange={event => onPatch(device.id, {barge_in:event.target.checked}, `barge_in → ${event.target.checked}`)}/> Barge-in</label>}
            <p className="device-muted">{chatOnly ? "Chat-only: voice features are inactive." : voiceMode === "websocket" ? "WebSocket uses turn-based audio without barge-in." : effectiveMode === "realtime" ? "Realtime handles interruptions through the selected provider." : "Barge-in lets you interrupt a spoken response."} Changes apply when the device reconnects.</p>
            <h4>Button Actions</h4>
            {GESTURE_ROWS.map(row => <GestureRow key={row.id} label={row.label} action={device.config?.button_actions?.[row.id]} webhooks={webhooks} disabled={saving} onChange={next => {
              const merged = {...device.config?.button_actions};
              if (!next || next.kind === "none") delete merged[row.id]; else merged[row.id] = next;
              onPatch(device.id, {button_actions:merged}, `${row.label} → ${next?.kind ?? "none"}`);
            }}/>) }
            {saveStatus?.status === "error" && <p role="alert">{saveStatus.message || "Save failed"}</p>}
            <button ref={diagnosticsButton} type="button" onClick={() => setDiagnosticsOpen(true)}>Device Diagnostics</button>
          </section>
          <section className="device-section" aria-label="Device metrics & logging">
            <h3>Device metrics &amp; logging</h3>
            <DeviceMetrics deviceId={device.id}/>
            <details><summary>Session activity log</summary><p className="device-muted">Actions recorded for this device in this browser session.</p><div className="h-48"><EventLog entries={logs.filter(entry => entry.deviceId === device.id)}/></div></details>
          </section>
          {diagnosticsOpen && <Modal title="Device Diagnostics" onClose={() => setDiagnosticsOpen(false)} returnFocus={diagnosticsButton} className="device-diagnostics-modal">
            <p className="device-muted">Commands target {device.config?.name || device.name}. Command delivery is not device acknowledgment.</p>
            <div className="device-diagnostic-grid">
              <form onSubmit={event => { event.preventDefault(); void handleCommand("set_volume"); }}><h3>Volume</h3><label className="device-field">Volume<input type="number" min={0} max={100} required value={ctlVolume} onChange={event => setCtlVolume(event.target.value)}/></label><button disabled={commandBusy} type="submit">Set volume</button></form>
              <section><h3>Audio</h3><button disabled={commandBusy} type="button" onClick={() => void handleCommand("mute")}>Mute</button><button disabled={commandBusy} type="button" onClick={() => void handleCommand("unmute")}>Unmute</button></section>
              <section><h3>Restart</h3><button disabled={commandBusy} type="button" onClick={() => void handleCommand("reboot")}>Reboot device</button></section>
              <form onSubmit={event => { event.preventDefault(); void handleCommand("ota"); }}><h3>Firmware update</h3><label className="device-field">Firmware URL<input type="url" value={ctlOtaUrl} onChange={event => setCtlOtaUrl(event.target.value)} placeholder={otaPlaceholder}/></label><button disabled={commandBusy} type="submit">Install firmware</button></form>
              <section><h3>Barge-in command</h3><label className="device-switch"><input role="switch" type="checkbox" aria-label="Command barge-in enabled" checked={ctlBargeIn === "true"} onChange={event => setCtlBargeIn(String(event.target.checked))}/> Enabled</label><button disabled={commandBusy || chatOnly || voiceMode !== "webrtc" || effectiveMode === "realtime"} type="button" onClick={() => void handleCommand("set_barge_in")}>Set barge-in</button></section>
              <section><h3>Audio output</h3><label className="device-field">Sample rate<select aria-label="Sample rate" value={sampleRate ?? ""} disabled={saving || chatOnly} onChange={event => onPatch(device.id, {output_sample_rate:event.target.value ? Number(event.target.value) : null}, `output_sample_rate → ${event.target.value || "default"}`)}><option value="">default</option>{[16000,22050,24000,44100].map(rate => <option key={rate} value={rate}>{rate}</option>)}</select></label></section>
            </div>
            {commandStatus && <p role="status">{commandStatus}</p>}
            {ctlError && <p role="alert">{ctlError}</p>}
          </Modal>}

        </div>
      )}
    </li>
  );
}

function GestureRow({
  label,
  action,
  webhooks,
  disabled,
  onChange,
}: {
  label: string;
  action?: ButtonAction;
  webhooks: ApiWebhook[];
  disabled: boolean;
  onChange: (next: ButtonAction | undefined) => void;
}) {
  const [kindDraft, setKindDraft] = useState<ActionKind>(
    action?.kind ?? "none",
  );
  const [textDraft, setTextDraft] = useState(action?.text ?? "");
  useEffect(() => {
    setKindDraft(action?.kind ?? "none");
  }, [action?.kind]);
  useEffect(() => {
    setTextDraft(action?.text ?? "");
  }, [action?.text]);

  const commitKind = (nextKind: ActionKind) => {
    setKindDraft(nextKind);
    if (nextKind === "none") {
      onChange(undefined);
      return;
    }
    if (nextKind === "prompt" || nextKind === "announce") {
      const text = textDraft.trim();
      if (text) onChange({ kind: nextKind, text });
      return;
    }
    if (nextKind === "command") {
      onChange({ kind: "command", command: action?.command ?? "mute" });
      return;
    }
    const id = action?.webhook_id ?? webhooks[0]?.id;
    if (!id) return;
    onChange({ kind: "webhook", webhook_id: id });
  };

  const commitText = () => {
    const trimmed = textDraft.trim();
    if (kindDraft !== "prompt" && kindDraft !== "announce") return;
    if (trimmed === (action?.text ?? "") && action?.kind === kindDraft) return;
    if (!trimmed) {
      onChange(undefined);
      setKindDraft("none");
      return;
    }
    onChange({ kind: kindDraft, text: trimmed });
  };

  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className={labelClass + " w-28"}>
        {label}
        <select
          className={inputClass}
          value={kindDraft}
          disabled={disabled}
          aria-label={`${label} action`}
          onChange={(e) => commitKind(e.target.value as ActionKind)}
        >
          {ACTION_KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      {(kindDraft === "prompt" || kindDraft === "announce") && (
        <label className={labelClass + " min-w-[180px] flex-1"}>
          Text
          <input
            className={inputClass}
            value={textDraft}
            disabled={disabled}
            aria-label={`${label} text`}
            onChange={(e) => setTextDraft(e.target.value)}
            onBlur={commitText}
            placeholder={
              kindDraft === "prompt"
                ? "turn off the kitchen lights"
                : "Goodnight"
            }
          />
        </label>
      )}
      {kindDraft === "command" && (
        <>
          <label className={labelClass}>
            Command
            <select
              className={inputClass}
              value={action?.command ?? "mute"}
              disabled={disabled}
              aria-label={`${label} command`}
              onChange={(e) => {
                const command = e.target.value;
                if (command === "set_volume") {
                  onChange({
                    kind: "command",
                    command,
                    volume: action?.volume ?? 50,
                  });
                } else {
                  onChange({ kind: "command", command });
                }
              }}
            >
              {BUTTON_COMMANDS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          {action?.command === "set_volume" && (
            <label className={labelClass}>
              Volume
              <input
                type="number"
                min={0}
                max={100}
                className={inputClass + " w-20"}
                value={action.volume ?? 50}
                disabled={disabled}
                aria-label={`${label} volume`}
                onChange={(e) =>
                  onChange({
                    kind: "command",
                    command: "set_volume",
                    volume: Number(e.target.value),
                  })
                }
              />
            </label>
          )}
        </>
      )}
      {kindDraft === "webhook" && (
        <label className={labelClass + " min-w-[160px] flex-1"}>
          Webhook
          <select
            className={inputClass}
            value={action?.webhook_id ?? ""}
            disabled={disabled || webhooks.length === 0}
            aria-label={`${label} webhook`}
            onChange={(e) =>
              onChange({ kind: "webhook", webhook_id: e.target.value })
            }
          >
            {webhooks.length === 0 ? (
              <option value="">Add a webhook in Settings</option>
            ) : (
              webhooks.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))
            )}
          </select>
        </label>
      )}
    </div>
  );
}

function SaveBadge({ status }: { status?: SaveStatus }) {
  if (!status) return null;
  if (status.status === "saving")
    return <span role="status" className="text-[11px] text-zinc-400">Saving…</span>;
  if (status.status === "saved")
    return <span role="status" className="text-[11px] text-emerald-400">Saved</span>;
  return (
    <span className="text-[11px] text-red-400" title={status.message}>
      Error
    </span>
  );
}
