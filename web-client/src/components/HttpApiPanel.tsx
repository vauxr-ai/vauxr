import { useCallback, useEffect, useState } from "react";
import { type ApiDevice, deriveHttpUrl, useHttpApi } from "../hooks/useHttpApi";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";

const STATE_COLORS: Record<string, string> = {
  idle: "bg-gray-500",
  listening: "bg-blue-500",
  processing: "bg-yellow-500",
  speaking: "bg-green-500",
};

const COMMANDS = ["set_volume", "mute", "unmute", "reboot"] as const;

interface Props {
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  addLog: (dir: LogEntry["dir"], text: string) => void;
}

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

  if (wsState === "disconnected") return null;

  const selectClass =
    "rounded bg-gray-800 px-3 py-1.5 text-sm text-white outline-none focus:ring-1 focus:ring-indigo-500";
  const inputClass = selectClass;
  const btnClass =
    "rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-700";
  const errorClass = "text-xs text-red-400 mt-1";

  return (
    <div className="rounded border border-gray-700 bg-gray-900 text-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-700 px-4 py-2">
        <span className="font-semibold text-gray-200">Vauxr Local Server</span>
        <button
          className="text-xs text-gray-400 hover:text-white"
          onClick={refreshDevices}
        >
          Refresh &#8635;
        </button>
      </div>

      {/* Devices */}
      <div className="border-b border-gray-700 px-4 py-3">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
          Devices
        </div>
        {devicesError && <p className={errorClass}>{devicesError}</p>}
        {devices.length === 0 && !devicesError && (
          <p className="text-xs text-gray-500">No devices</p>
        )}
        <ul className="space-y-1">
          {devices.map((d) => (
            <li key={d.id} className="flex items-center gap-2 text-gray-300">
              <span
                className={`inline-block h-2 w-2 rounded-full ${STATE_COLORS[d.state] ?? "bg-gray-500"}`}
              />
              <span className="flex-1">{d.name || d.id}</span>
              <span className="text-xs text-gray-500">{d.state}</span>
              <button
                className="text-xs text-indigo-400 hover:text-indigo-300"
                onClick={() => {
                  setAnnDeviceId(d.id);
                }}
              >
                Announce
              </button>
            </li>
          ))}
        </ul>
      </div>

      {/* Announce */}
      <div className="border-b border-gray-700 px-4 py-3">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
          Announce
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Device
            <select
              className={selectClass}
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
          <label className="flex flex-1 flex-col gap-1 text-xs text-gray-400">
            Text
            <input
              className={inputClass}
              value={annText}
              onChange={(e) => setAnnText(e.target.value)}
              placeholder="Hello from the browser"
            />
          </label>
          <button className={btnClass} onClick={handleAnnounce}>
            Send
          </button>
        </div>
        {annError && <p className={errorClass}>{annError}</p>}
      </div>

      {/* Control */}
      <div className="px-4 py-3">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
          Control
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Device
            <select
              className={selectClass}
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
          <label className="flex flex-col gap-1 text-xs text-gray-400">
            Command
            <select
              className={selectClass}
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
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              Volume
              <input
                type="number"
                min={0}
                max={100}
                className={`${inputClass} w-20`}
                value={ctlVolume}
                onChange={(e) => setCtlVolume(e.target.value)}
              />
            </label>
          )}
          <button className={btnClass} onClick={handleCommand}>
            Send
          </button>
        </div>
        {ctlError && <p className={errorClass}>{ctlError}</p>}
      </div>
    </div>
  );
}
