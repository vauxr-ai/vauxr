import { useCallback, useEffect, useState } from "react";
import { type ApiDevice, deriveHttpUrl, useHttpApi } from "../hooks/useHttpApi";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import Icon from "./Icon";

const STATE_DOT: Record<string, string> = {
  idle: "bg-zinc-500",
  listening: "bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.6)]",
  processing: "bg-amber-400 shadow-[0_0_8px_rgba(245,158,11,0.6)]",
  speaking: "bg-violet-400 shadow-[0_0_8px_rgba(124,58,237,0.6)]",
};

const COMMANDS = ["set_volume", "mute", "unmute", "reboot"] as const;

interface Props {
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  addLog: (dir: LogEntry["dir"], text: string) => void;
}

const inputClass =
  "rounded-lg border border-white/5 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/30";

const labelClass =
  "flex flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500";

const primaryBtn =
  "focus-ring rounded-lg bg-indigo-500 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50";

const ghostBtn =
  "focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-400 hover:bg-white/5 hover:text-zinc-200";

export default function HttpApiPanel({ wsUrl, token, wsState, addLog }: Props) {
  const httpUrl = deriveHttpUrl(wsUrl);
  const api = useHttpApi(httpUrl, token);

  const [devices, setDevices] = useState<ApiDevice[]>([]);
  const [devicesError, setDevicesError] = useState("");

  const [annDeviceId, setAnnDeviceId] = useState("");
  const [annText, setAnnText] = useState("");
  const [annError, setAnnError] = useState("");

  const [ctlDeviceId, setCtlDeviceId] = useState("");
  const [ctlCommand, setCtlCommand] = useState<string>(COMMANDS[0]);
  const [ctlVolume, setCtlVolume] = useState("50");
  const [ctlError, setCtlError] = useState("");

  const refreshDevices = useCallback(async () => {
    setDevicesError("");
    try {
      const list = await api.listDevices();
      setDevices(list);
      if (list.length > 0) {
        setAnnDeviceId((prev) => prev || list[0].id);
        setCtlDeviceId((prev) => prev || list[0].id);
      }
    } catch (err) {
      setDevicesError(err instanceof Error ? err.message : String(err));
    }
  }, [api]);

  useEffect(() => {
    if (wsState !== "disconnected") {
      refreshDevices();
    }
    // refreshDevices intentionally omitted: api is recreated each render,
    // so including it would cause an infinite refresh loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsState]);

  const handleAnnounce = async () => {
    setAnnError("");
    try {
      await api.announce(annDeviceId, annText);
      addLog("sys", `Announce sent to ${annDeviceId}: "${annText}"`);
      setAnnText("");
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
        ctlCommand === "set_volume" ? { volume: Number(ctlVolume) } : undefined;
      await api.command(ctlDeviceId, ctlCommand, params);
      addLog("sys", `Command ${ctlCommand} sent to ${ctlDeviceId}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setCtlError(msg);
      addLog("sys", `Command error: ${msg}`);
    }
  };

  if (wsState === "disconnected") {
    return (
      <div className="card p-6 text-sm text-zinc-500">
        Connect to a server to use the HTTP API.
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">HTTP API</h2>
          <p className="text-xs text-zinc-500">
            Announce, control, and inspect devices over the local server.
          </p>
        </div>
        <button className={ghostBtn} onClick={refreshDevices}>
          <Icon name="refresh" size={14} />
          Refresh
        </button>
      </div>

      <Section title="Devices">
        {devicesError && <ErrorRow message={devicesError} />}
        {devices.length === 0 && !devicesError && (
          <p className="text-xs text-zinc-500">No devices</p>
        )}
        <ul className="space-y-1">
          {devices.map((d) => (
            <li
              key={d.id}
              className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-white/5"
            >
              <span
                className={`inline-block h-2 w-2 rounded-full ${STATE_DOT[d.state] ?? "bg-zinc-500"}`}
              />
              <span className="flex-1 truncate text-zinc-200">{d.name || d.id}</span>
              <span className="text-[11px] uppercase tracking-wider text-zinc-500">
                {d.state}
              </span>
              <button
                className="focus-ring text-[11px] font-medium text-indigo-300 hover:text-indigo-200"
                onClick={() => setAnnDeviceId(d.id)}
              >
                Announce
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Announce">
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>
            Device
            <select
              className={inputClass}
              value={annDeviceId}
              onChange={(e) => setAnnDeviceId(e.target.value)}
            >
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name || d.id}
                </option>
              ))}
            </select>
          </label>
          <label className={labelClass + " flex-1"}>
            Text
            <input
              className={inputClass}
              value={annText}
              onChange={(e) => setAnnText(e.target.value)}
              placeholder="Hello from the browser"
            />
          </label>
          <button className={primaryBtn} onClick={handleAnnounce}>
            Send
          </button>
        </div>
        {annError && <ErrorRow message={annError} />}
      </Section>

      <Section title="Control" lastSection>
        <div className="flex flex-wrap items-end gap-3">
          <label className={labelClass}>
            Device
            <select
              className={inputClass}
              value={ctlDeviceId}
              onChange={(e) => setCtlDeviceId(e.target.value)}
            >
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name || d.id}
                </option>
              ))}
            </select>
          </label>
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
          <button className={primaryBtn} onClick={handleCommand}>
            Send
          </button>
        </div>
        {ctlError && <ErrorRow message={ctlError} />}
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
  lastSection,
}: {
  title: string;
  children: React.ReactNode;
  lastSection?: boolean;
}) {
  return (
    <div className={lastSection ? "px-5 py-4" : "border-b border-white/5 px-5 py-4"}>
      <div className="card-section-title mb-3">{title}</div>
      {children}
    </div>
  );
}

function ErrorRow({ message }: { message: string }) {
  return (
    <p className="mt-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
      {message}
    </p>
  );
}
