import { useCallback, useEffect, useRef, useState } from "react";
import { deriveHttpUrl } from "../hooks/useHttpApi";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";

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

const STATE_COLORS: Record<string, string> = {
  idle: "bg-gray-500",
  listening: "bg-blue-500",
  processing: "bg-yellow-500",
  speaking: "bg-green-500",
  offline: "bg-gray-700",
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

  if (wsState === "disconnected") return null;

  return (
    <div className="rounded border border-gray-700 bg-gray-900 text-sm">
      <div className="flex items-center justify-between border-b border-gray-700 px-4 py-2">
        <span className="font-semibold text-gray-200">Devices</span>
        <button
          className="text-xs text-gray-400 hover:text-white"
          onClick={refresh}
        >
          Refresh &#8635;
        </button>
      </div>

      <div className="px-4 py-3">
        {error && <p className="text-xs text-red-400 mb-2">{error}</p>}
        {devices.length === 0 && !error && (
          <p className="text-xs text-gray-500">No devices connected</p>
        )}
        <ul className="space-y-2">
          {devices.map((d) => {
            const mode: FollowUpMode = d.config?.follow_up_mode ?? "auto";
            const sampleRate = d.config?.output_sample_rate;
            const status = saveStatus[d.id];
            return (
              <li
                key={d.id}
                className="flex flex-wrap items-center gap-3 rounded bg-gray-800/50 px-3 py-2"
              >
                <span
                  className={`inline-block h-2 w-2 rounded-full ${STATE_COLORS[d.state] ?? "bg-gray-500"}`}
                  title={d.state}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-gray-200 font-medium truncate">
                    {d.name || d.id}
                  </div>
                  <div className="text-[11px] text-gray-500">
                    {d.state} · last seen {formatLastSeen(d.lastSeen)}
                  </div>
                </div>

                <label className="flex items-center gap-2 text-xs text-gray-400">
                  Follow-up
                  <select
                    className="rounded bg-gray-800 px-2 py-1 text-xs text-white outline-none focus:ring-1 focus:ring-indigo-500"
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

                <label className="flex items-center gap-2 text-xs text-gray-400">
                  Sample rate
                  <select
                    className="rounded bg-gray-800 px-2 py-1 text-xs text-white outline-none focus:ring-1 focus:ring-indigo-500"
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
                  <span className="text-[11px] text-gray-400">Saving…</span>
                )}
                {status?.status === "saved" && (
                  <span className="text-[11px] text-green-400">Saved</span>
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
