import { useEffect, useRef, useState } from "react";
import { browserIdentity, maintainIdentity } from "../auth/browser";

export function validateVoiceUrl(value: string) {
  const url = new URL(value);
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  if (
    url.protocol !== scheme ||
    url.host !== location.host ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname !== "/ws"
  )
    throw new Error(
      "Use the current server authority (hostname and port), matching WS/WSS transport and /ws path. No credentials or query strings.",
    );
  return url.href;
}
interface Props {
  connected: boolean;
  onConnect: (url: string, deviceId: string, token: string) => void;
  onDisconnect: () => void;
}
export default function ConfigPanel({
  connected,
  onConnect,
  onDisconnect,
}: Props) {
  const [url, setUrl] = useState(
    `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`,
  );
  const [message, setMessage] = useState("");
  const [active, setActive] = useState(false);
  const release = useRef<() => void>();
  const stopRequested = useRef(false);
  const disconnect = useRef(onDisconnect);
  disconnect.current = onDisconnect;
  useEffect(() => {
    const stop = () => {
      stopRequested.current = true;
      disconnect.current();
      release.current?.();
    };
    const channel = new BroadcastChannel("vauxr-voice");
    channel.onmessage = stop;
    window.addEventListener("voice-stop", stop);
    window.addEventListener("pagehide", stop);
    return () => {
      stop();
      channel.close();
      window.removeEventListener("voice-stop", stop);
      window.removeEventListener("pagehide", stop);
    };
  }, []);
  async function connect(recover: boolean) {
    setMessage("");
    try {
      const endpoint = validateVoiceUrl(url);
      if (!isSecureContext || !navigator.locks || !crypto.subtle)
        throw new Error(
          "Browser voice requires HTTPS or localhost and Web Locks/WebCrypto support. HTTP administration remains available.",
        );
      stopRequested.current = false;
      await navigator.locks.request(
        "vauxr-browser-voice",
        { ifAvailable: true },
        async (lock) => {
          if (!lock)
            throw new Error(
              "Voice is active in another tab. Disconnect it first.",
            );
          setActive(true);
          let timer: number | undefined;
          try {
            const identity = await browserIdentity(recover);
            if (stopRequested.current) return;
            onConnect(endpoint, identity.deviceId, identity.token!);
            let polling = false;
            timer = window.setInterval(
              async () => {
                if (polling || stopRequested.current) return;
                polling = true;
                try {
                  const old = identity.token;
                  await maintainIdentity(identity);
                  if (old !== identity.token && !stopRequested.current) {
                    disconnect.current();
                    onConnect(endpoint, identity.deviceId, identity.token!);
                  }
                } catch {
                  setMessage(
                    "Browser access could not be verified. Disconnected. Retry after one minute; revoked or lost credentials require explicit recovery.",
                  );
                  disconnect.current();
                  release.current?.();
                } finally {
                  polling = false;
                }
              },
              60000 + Math.random() * 5000,
            );
            await new Promise<void>((resolve) => {
              release.current = resolve;
            });
            // Let a running durable-save/ACK finish before releasing logout's lock.
            while (polling)
              await new Promise((resolve) => setTimeout(resolve, 25));
          } finally {
            window.clearInterval(timer);
            release.current = undefined;
            setActive(false);
          }
        },
      );
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Browser enrollment failed.");
    }
  }
  return (
    <div className="auth-panel card space-y-4 p-5">
      <h2>Browser voice connection</h2>
      <p>
        Connect enrolls this browser through your owner session as a separate
        scoped device. Its key and credential stay in this browser profile's
        IndexedDB; one tab uses voice at a time. Reload requires Connect. Logout
        revokes browser access and clears its credential; recovery reuses its
        saved key.
      </p>
      <label>
        Voice WebSocket URL
        <input
          className="block w-full bg-zinc-800 p-2"
          value={url}
          disabled={active}
          onChange={(e) => setUrl(e.target.value)}
        />
      </label>
      <p>
        {connected
          ? "Connected"
          : active
            ? "Connecting or disconnected transport; disconnect before retrying."
            : "Disconnected"}
      </p>
      {message && <p role="alert">{message}</p>}
      {active ? (
        <button
          onClick={() => {
            stopRequested.current = true;
            onDisconnect();
            release.current?.();
          }}
        >
          Disconnect
        </button>
      ) : (
        <>
          <button onClick={() => connect(false)}>Connect browser voice</button>{" "}
          <button
            onClick={() => {
              if (
                window.confirm(
                  "Retire this browser identity’s current access and re-enroll with its saved key?",
                )
              )
                void connect(true);
            }}
          >
            Recover browser identity
          </button>
        </>
      )}
    </div>
  );
}
