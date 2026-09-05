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
  follow_up_mode?: FollowUpMode;
  output_sample_rate?: number;
  barge_in?: boolean;
  button_actions?: Partial<Record<GestureId, ButtonAction>>;
}

interface ApiDeviceWithConfig {
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
const COMMANDS = ["set_volume", "mute", "unmute", "reboot", "ota", "set_barge_in"] as const;
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
  const { listWebhooks } = useWebhooks(baseUrl, token);

  const [devices, setDevices] = useState<ApiDeviceWithConfig[]>([]);
  const [webhooks, setWebhooks] = useState<ApiWebhook[]>([]);
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
      try {
        setWebhooks(await listWebhooks());
      } catch {
        // Webhooks are optional for the devices list; gesture editors degrade.
      }
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
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
                httpBase={baseUrl}
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
  device: ApiDeviceWithConfig;
  expanded: boolean;
  onToggle: () => void;
  onPatch: (id: string, patch: Partial<DeviceConfig>, label: string) => void;
  saveStatus?: SaveStatus;
  api: ReturnType<typeof useHttpApi>;
  addLog: (dir: LogEntry["dir"], text: string) => void;
  httpBase: string;
  webhooks: ApiWebhook[];
}

function DeviceCard({
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
  const pill = STATE_PILL[device.state] ?? STATE_PILL.offline;
  const dot = STATE_DOT[device.state] ?? STATE_DOT.offline;
  const mode: FollowUpMode = device.config?.follow_up_mode ?? "auto";
  const bargeIn = device.config?.barge_in !== false;
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
  const [ctlOtaUrl, setCtlOtaUrl] = useState("");
  const [ctlBargeIn, setCtlBargeIn] = useState("true");
  const [ctlError, setCtlError] = useState("");
  const otaPlaceholder = `${httpBase}/firmware/${device.platform || "satellite1"}.bin`;

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
      const params =
        ctlCommand === "set_volume"
          ? { volume: Number(ctlVolume) }
          : ctlCommand === "ota"
            ? { url: ctlOtaUrl.trim() || otaPlaceholder }
            : ctlCommand === "set_barge_in"
              ? { enabled: ctlBargeIn === "true" }
              : undefined;
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
                Barge-in
                <select
                  className={inputClass}
                  value={bargeIn ? "on" : "off"}
                  onChange={(e) =>
                    onPatch(
                      device.id,
                      { barge_in: e.target.value === "on" },
                      `barge_in → ${e.target.value}`,
                    )
                  }
                  disabled={saving}
                >
                  <option value="on">on</option>
                  <option value="off">off</option>
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
            <div className="space-y-2">
              <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-500">
                Action button
              </div>
              {GESTURE_ROWS.map((row) => (
                <GestureRow
                  key={row.id}
                  label={row.label}
                  action={device.config?.button_actions?.[row.id]}
                  webhooks={webhooks}
                  disabled={saving}
                  onChange={(next) => {
                    const merged: Partial<Record<GestureId, ButtonAction>> = {
                      ...(device.config?.button_actions ?? {}),
                    };
                    if (!next || next.kind === "none") {
                      delete merged[row.id];
                    } else {
                      merged[row.id] = next;
                    }
                    onPatch(
                      device.id,
                      { button_actions: merged },
                      `${row.label} → ${next?.kind ?? "none"}`,
                    );
                  }}
                />
              ))}
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
              {ctlCommand === "ota" && (
                <label className={labelClass + " flex-1 min-w-[220px]"}>
                  Firmware URL
                  <input
                    className={inputClass}
                    value={ctlOtaUrl}
                    onChange={(e) => setCtlOtaUrl(e.target.value)}
                    placeholder={otaPlaceholder}
                    aria-label="Firmware URL"
                  />
                </label>
              )}
              {ctlCommand === "set_barge_in" && (
                <label className={labelClass}>
                  Enabled
                  <select
                    className={inputClass}
                    value={ctlBargeIn}
                    onChange={(e) => setCtlBargeIn(e.target.value)}
                    aria-label="Barge-in enabled"
                  >
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
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
  const [kindDraft, setKindDraft] = useState<ActionKind>(action?.kind ?? "none");
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
            placeholder={kindDraft === "prompt" ? "turn off the kitchen lights" : "Goodnight"}
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
                  onChange({ kind: "command", command, volume: action?.volume ?? 50 });
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
            onChange={(e) => onChange({ kind: "webhook", webhook_id: e.target.value })}
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
  if (status.status === "saving") return <span className="text-[11px] text-zinc-400">Saving…</span>;
  if (status.status === "saved") return <span className="text-[11px] text-emerald-400">Saved</span>;
  return (
    <span className="text-[11px] text-red-400" title={status.message}>
      Error
    </span>
  );
}
