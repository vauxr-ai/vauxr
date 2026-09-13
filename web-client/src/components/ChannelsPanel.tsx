import { useEffect, useState } from "react";
import { useChannels, type ApiChannel } from "../hooks/useChannels";
export default function ChannelsPanel() {
  const api = useChannels();
  const [channels, setChannels] = useState<ApiChannel[]>([]);
  const [error, setError] = useState("");
  const refresh = async () => {
    try {
      setChannels(await api.listChannels());
      setError("");
    } catch {
      setError("Unable to load routing channels.");
    }
  };
  useEffect(() => {
    void refresh();
  }, []);
  return (
    <div className="auth-panel card space-y-4 p-5">
      <h2>Routing channels</h2>
      <p>
        New OpenClaw connections require owner-approved integration enrollment. Manage its
        credentials in Pairing and access.
      </p>
      {error && <p role="alert">{error}</p>}
      <button onClick={refresh}>Refresh channels</button>
      {channels.map((c) => (
        <div key={c.id}>
          {c.name} — {c.type} — {c.active ? "Active route" : "Inactive route"}{" "}
          <button
            disabled={c.active}
            onClick={async () => {
              try {
                await api.activateChannel(c.id);
                await refresh();
              } catch {
                setError("Channel activation failed. Refresh before retrying.");
              }
            }}
          >
            Activate
          </button>
        </div>
      ))}
    </div>
  );
}
