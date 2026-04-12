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
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-sm text-gray-400">
        Server URL
        <input
          className="rounded bg-gray-800 px-3 py-1.5 text-sm text-white outline-none focus:ring-1 focus:ring-indigo-500"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={handleUrlKeyDown}
          disabled={connected}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm text-gray-400">
        Device ID
        <input
          className="rounded bg-gray-800 px-3 py-1.5 text-sm text-white outline-none focus:ring-1 focus:ring-indigo-500"
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
          disabled={connected}
        />
      </label>

      <label className="flex flex-col gap-1 text-sm text-gray-400">
        Token
        <input
          type="password"
          className="rounded bg-gray-800 px-3 py-1.5 text-sm text-white outline-none focus:ring-1 focus:ring-indigo-500"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          disabled={connected}
        />
      </label>

      <button
        className={`rounded px-4 py-1.5 text-sm font-medium ${
          connected
            ? "bg-red-600 hover:bg-red-700"
            : "bg-indigo-600 hover:bg-indigo-700"
        }`}
        onClick={() =>
          connected ? onDisconnect() : onConnect(url, deviceId, token)
        }
      >
        {connected ? "Disconnect" : "Connect"}
      </button>
    </div>
  );
}
