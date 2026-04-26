import { useState, type KeyboardEvent } from "react";

interface Props {
  connected: boolean;
  onConnect: (url: string, deviceId: string, token: string) => void;
  onDisconnect: () => void;
}

const WS_PORT = 8765;

function defaultServerUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = window.location.hostname || "localhost";
  return `${protocol}//${host}:${WS_PORT}`;
}

const inputClass =
  "rounded-lg border border-white/5 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/30 disabled:opacity-60";

const labelClass =
  "flex flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500";

export default function ConfigPanel({ connected, onConnect, onDisconnect }: Props) {
  const [url, setUrl] = useState(defaultServerUrl);
  const [deviceId, setDeviceId] = useState("test-web-client");
  const [token, setToken] = useState("anything-you-want");

  const handleUrlKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !connected) {
      onConnect(url, deviceId, token);
    }
  };

  return (
    <div className="card p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">Connection</h2>
          <p className="text-xs text-zinc-500">
            WebSocket bridge to your Vauxr server.
          </p>
        </div>
        <span
          className={`pill ${
            connected
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              : "border-zinc-700/50 bg-zinc-800/50 text-zinc-400"
          }`}
        >
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${
              connected ? "bg-emerald-400" : "bg-zinc-500"
            }`}
          />
          {connected ? "Online" : "Offline"}
        </span>
      </div>

      <div className="grid gap-4 md:grid-cols-[2fr,1fr,1fr,auto] md:items-end">
        <label className={labelClass}>
          Server URL
          <input
            className={inputClass}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={handleUrlKeyDown}
            disabled={connected}
          />
        </label>

        <label className={labelClass}>
          Device ID
          <input
            className={inputClass}
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            disabled={connected}
          />
        </label>

        <label className={labelClass}>
          Token
          <input
            type="password"
            className={inputClass}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            disabled={connected}
          />
        </label>

        <button
          type="button"
          className={`focus-ring rounded-lg px-5 py-2 text-sm font-semibold transition-colors ${
            connected
              ? "bg-red-500/90 text-white hover:bg-red-500"
              : "bg-indigo-500 text-white hover:bg-indigo-400 shadow-[0_0_20px_-5px_rgba(99,102,241,0.5)]"
          }`}
          onClick={() =>
            connected ? onDisconnect() : onConnect(url, deviceId, token)
          }
        >
          {connected ? "Disconnect" : "Connect"}
        </button>
      </div>
    </div>
  );
}
