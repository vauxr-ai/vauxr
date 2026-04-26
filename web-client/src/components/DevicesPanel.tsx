import { useCallback, useEffect, useRef, useState } from "react";
import { deriveHttpUrl } from "../hooks/useHttpApi";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import Icon from "./Icon";

type FollowUpMode = "auto" | "always" | "never";

interface DeviceConfig {
  name?: string;
  voice?: boolean;
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

const selectClass =
  "rounded-md border border-white/5 bg-zinc-900/80 px-2 py-1 text-xs text-zinc-100 outline-none focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/30 disabled:opacity-60";

const ghostBtn =
  "focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-400 hover:bg-white/5 hover:text-zinc-200";

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

  const [devices, setDevices] = useState<ApiDeviceWithConfig[]>([]);
  const [error, setError] = useState("");
  const [saveStatus, setSaveStatus] = useState<Record<string, SaveStatus>>({});

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
      const body = await res.json() as ApiDeviceWithConfig[];
      setDevices(body);
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
            Connected hardware and per-device settings.
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
        {devices.length === 0 && !error && (
          <p className="text-xs text-zinc-500">No devices connected.</p>
        )}
        <ul className="space-y-2">
          {devices.map((d) => {
            const mode: FollowUpMode = d.config?.follow_up_mode ?? "auto";
            const sampleRate = d.config?.output_sample_rate;
            const status = saveStatus[d.id];
            const pill = STATE_PILL[d.state] ?? STATE_PILL.offline;
            const dot = STATE_DOT[d.state] ?? STATE_DOT.offline;
            return (
              <li
                key={d.id}
                className="flex flex-wrap items-center gap-3 rounded-lg border border-white/5 bg-zinc-900/40 px-3 py-3"
              >
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-zinc-800/80 text-zinc-400">
                  <Icon name="devices" size={16} />
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium text-zinc-200">
                      {d.name || d.id}
                    </span>
                    <span className={`pill ${pill}`}>
                      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${dot}`} />
                      {d.state}
                    </span>
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    {d.id} · last seen {formatLastSeen(d.lastSeen)}
                  </div>
                </div>

                <label className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
                  Follow-up
                  <select
                    className={selectClass}
                    value={mode}
                    onChange={(e) => updateDeviceConfig(d.id, { follow_up_mode: e.target.value as FollowUpMode }, `follow_up_mode → ${e.target.value}`)}
                    disabled={status?.status === "saving"}
                  >
                    {FOLLOW_UP_OPTIONS.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
                  Sample rate
                  <select
                    className={selectClass}
                    value={sampleRate ?? ""}
                    onChange={(e) => {
                      const val = e.target.value;
                      const patch = val === "" ? { output_sample_rate: undefined } : { output_sample_rate: parseInt(val, 10) };
                      updateDeviceConfig(d.id, patch, `output_sample_rate → ${val || "default"}`);
                    }}
                    disabled={status?.status === "saving"}
                  >
                    <option value="">default</option>
                    <option value="16000">16000</option>
                    <option value="22050">22050</option>
                    <option value="24000">24000</option>
                    <option value="44100">44100</option>
                  </select>
                </label>

                {status?.status === "saving" && (
                  <span className="text-[11px] text-zinc-400">Saving…</span>
                )}
                {status?.status === "saved" && (
                  <span className="text-[11px] text-emerald-400">Saved</span>
                )}
                {status?.status === "error" && (
                  <span className="text-[11px] text-red-400" title={status.message}>
                    Error
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
