import { useCallback, useEffect, useRef, useState } from "react";
import { deriveHttpUrl, useHttpApi } from "../hooks/useHttpApi";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import Icon from "./Icon";

type FollowUpMode = "auto" | "always" | "never";

interface DeviceConfig {
  name?: string;
  follow_up_mode?: FollowUpMode;
  output_sample_rate?: number;
}

interface ApiDeviceWithConfig {
  id: string;
  name: string;
  state: string;
  lastSeen: string;
  config: DeviceConfig;
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
const COMMANDS = ["set_volume", "mute", "unmute", "reboot"] as const;

interface SaveStatus {
  status: "saving" | "saved" | "error";
  message?: string;
}

interface Props {
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  addLog: (dir: LogEntry["dir"], text: string) => void;
}

const POLL_INTERVAL_MS = 5_000;

const inputClass =
  "rounded-md border border-white/5 bg-zinc-900/80 px-2 py-1 text-xs text-zinc-100 outline-none focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/30 disabled:opacity-60";

const primaryBtn =
  "focus-ring rounded-md bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50";

const ghostBtn =
  "focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-400 hover:bg-white/5 hover:text-zinc-200";

const labelClass =
  "flex flex-col gap-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-500";

function formatLastSeen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString();
}

export default function DevicesPanel({ wsUrl, token, wsState, addLog }: Props) {
  const baseUrl = deriveHttpUrl(wsUrl);
  const baseUrlRef = useRef(baseUrl);
  const tokenRef = useRef(token);
  baseUrlRef.current = baseUrl;
  tokenRef.current = token;

  const api = useHttpApi(baseUrl, token);

  const [devices, setDevices] = useState<ApiDeviceWithConfig[]>([]);
  const [error, setError] = useState("");
  const [saveStatus, setSaveStatus] = useState<Record<string, SaveStatus>>({});
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const refresh = useCallback(async () => {
    if (!baseUrlRef.current || !tokenRef.current) return;
    try {
      const res = await fetch(`${baseUrlRef.current}/api/devices`, {
        headers: { Authorization: `Bearer ${tokenRef.current}` },
      });
      if (!res.ok) {
        let msg = res.statusText;
        try {
          const body = await res.json();
          if (body.error) msg = body.error;
        } catch { /* fall through */ }
        throw new Error(msg);
      }
      const body = await res.json();
      const list: ApiDeviceWithConfig[] = Array.isArray(body) ? body : (body.devices ?? []);
      setDevices(list);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    if (wsState === "disconnected") return;
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [wsState, refresh]);

  const updateDeviceConfig = useCallback(
    async (deviceId: string, patch: Partial<DeviceConfig>, label: string) => {
      setSaveStatus((s) => ({ ...s, [deviceId]: { status: "saving" } }));
      try {
        const res = await fetch(`${baseUrlRef.current}/api/devices/${encodeURIComponent(deviceId)}`, {
          method: "PATCH",
          headers: {
            Authorization: `Bearer ${tokenRef.current}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(patch),
        });
        if (!res.ok) {
          let msg = res.statusText;
          try {
            const body = await res.json();
            if (body.error) msg = body.error;
          } catch { /* fall through */ }
          throw new Error(msg);
        }
        const updated = await res.json() as ApiDeviceWithConfig;
        setDevices((list) => list.map((d) => (d.id === deviceId ? updated : d)));
        setSaveStatus((s) => ({ ...s, [deviceId]: { status: "saved" } }));
        addLog("sys", `Device ${deviceId}: ${label}`);
        setTimeout(() => {
          setSaveStatus((s) => {
            if (s[deviceId]?.status !== "saved") return s;
            const { [deviceId]: _, ...rest } = s;
            return rest;
          });
        }, 1500);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setSaveStatus((s) => ({ ...s, [deviceId]: { status: "error", message: msg } }));
        addLog("sys", `Device ${deviceId} update error: ${msg}`);
      }
    },
    [addLog],
  );

  const toggleExpanded = useCallback((id: string) => {
    setExpanded((e) => ({ ...e, [id]: !e[id] }));
  }, []);

  if (wsState === "disconnected") {
    return (
      <div className="card p-6 text-sm text-zinc-500">
        Connect to a server to manage devices.
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">Devices</h2>
          <p className="text-xs text-zinc-500">
            Connected hardware, per-device settings, and actions.
          </p>
        </div>
        <button className={ghostBtn} onClick={refresh}>
          <Icon name="refresh" size={14} />
          Refresh
        </button>
      </div>

      <div className="px-5 py-4">
        {error && (
          <p className="mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}
        {devices.length === 0 && !error ? (
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
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface DeviceCardProps {
  device: ApiDeviceWithConfig;
  expanded: boolean;
  onToggle: () => void;
  onPatch: (id: string, patch: Partial<DeviceConfig>, label: string) => void;
  saveStatus?: SaveStatus;
  api: ReturnType<typeof useHttpApi>;
  addLog: (dir: LogEntry["dir"], text: string) => void;
}

function DeviceCard({
  device,
  expanded,
  onToggle,
  onPatch,
  saveStatus,
  api,
  addLog,
}: DeviceCardProps) {
  const pill = STATE_PILL[device.state] ?? STATE_PILL.offline;
  const dot = STATE_DOT[device.state] ?? STATE_DOT.offline;
  const mode: FollowUpMode = device.config?.follow_up_mode ?? "auto";
  const sampleRate = device.config?.output_sample_rate;

  const [nameDraft, setNameDraft] = useState(device.config?.name ?? device.name ?? "");
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

  const [annText, setAnnText] = useState("hello world");
  const [annError, setAnnError] = useState("");

  const [ctlCommand, setCtlCommand] = useState<string>(COMMANDS[0]);
  const [ctlVolume, setCtlVolume] = useState("50");
  const [ctlError, setCtlError] = useState("");

  const handleAnnounce = async () => {
    setAnnError("");
    const text = annText.trim() === "" ? "hello world" : annText;
    try {
      await api.announce(device.id, text);
      addLog("sys", `Announce sent to ${device.id}: "${text}"`);
      setAnnText("hello world");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setAnnError(msg);
      addLog("sys", `Announce error: ${msg}`);
    }
  };

  const handleCommand = async () => {
    setCtlError("");
    try {
      const params = ctlCommand === "set_volume" ? { volume: Number(ctlVolume) } : undefined;
      await api.command(device.id, ctlCommand, params);
      addLog("sys", `Command ${ctlCommand} sent to ${device.id}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setCtlError(msg);
      addLog("sys", `Command error: ${msg}`);
    }
  };

  const handleNameBlur = () => {
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
            {device.id} · last seen {formatLastSeen(device.lastSeen)}
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
        <div id={panelId} className="space-y-4 border-t border-white/5 px-3 py-3">
          <section aria-label="Device configuration" className="space-y-3">
            <div className="card-section-title">Config</div>
            <div className="flex flex-wrap items-end gap-3">
              <label className={labelClass}>
                Name
                <input
                  className={inputClass}
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  onBlur={handleNameBlur}
                  disabled={saving}
                />
              </label>
              <label className={labelClass}>
                Follow-up
                <select
                  className={inputClass}
                  value={mode}
                  onChange={(e) =>
                    onPatch(device.id, { follow_up_mode: e.target.value as FollowUpMode }, `follow_up_mode → ${e.target.value}`)
                  }
                  disabled={saving}
                >
                  {FOLLOW_UP_OPTIONS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label className={labelClass}>
                Sample rate
                <select
                  className={inputClass}
                  value={sampleRate ?? ""}
                  onChange={(e) => {
                    const val = e.target.value;
                    const patch =
                      val === ""
                        ? { output_sample_rate: undefined }
                        : { output_sample_rate: parseInt(val, 10) };
                    onPatch(device.id, patch, `output_sample_rate → ${val || "default"}`);
                  }}
                  disabled={saving}
                >
                  <option value="">default</option>
                  <option value="16000">16000</option>
                  <option value="22050">22050</option>
                  <option value="24000">24000</option>
                  <option value="44100">44100</option>
                </select>
              </label>
            </div>
            {saveStatus?.status === "error" && (
              <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {saveStatus.message ?? "Save failed"}
              </p>
            )}
          </section>

          <section aria-label="Announce" className="space-y-2">
            <div className="card-section-title">Announce</div>
            <div className="flex flex-wrap items-end gap-3">
              <label className={labelClass + " flex-1 min-w-[180px]"}>
                Text
                <input
                  className={inputClass}
                  value={annText}
                  onChange={(e) => setAnnText(e.target.value)}
                  placeholder="Hello from the browser"
                />
              </label>
              <button className={primaryBtn} onClick={handleAnnounce} type="button">
                Send
              </button>
            </div>
            {annError && (
              <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {annError}
              </p>
            )}
          </section>

          <section aria-label="Control" className="space-y-2">
            <div className="card-section-title">Control</div>
            <div className="flex flex-wrap items-end gap-3">
              <label className={labelClass}>
                Command
                <select
                  className={inputClass}
                  value={ctlCommand}
                  onChange={(e) => setCtlCommand(e.target.value)}
                >
                  {COMMANDS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
              {ctlCommand === "set_volume" && (
                <label className={labelClass}>
                  Volume
                  <input
                    type="number"
                    min={0}
                    max={100}
                    className={inputClass + " w-24"}
                    value={ctlVolume}
                    onChange={(e) => setCtlVolume(e.target.value)}
                  />
                </label>
              )}
              <button className={primaryBtn} onClick={handleCommand} type="button">
                Send
              </button>
            </div>
            {ctlError && (
              <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                {ctlError}
              </p>
            )}
          </section>
        </div>
      )}
    </li>
  );
}

function SaveBadge({ status }: { status?: SaveStatus }) {
  if (!status) return null;
  if (status.status === "saving") return <span className="text-[11px] text-zinc-400">Saving…</span>;
  if (status.status === "saved") return <span className="text-[11px] text-emerald-400">Saved</span>;
  return (
    <span className="text-[11px] text-red-400" title={status.message}>
      Error
    </span>
  );
}
