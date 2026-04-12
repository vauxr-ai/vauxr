import { useCallback, useEffect, useState } from "react";
import { deriveHttpUrl } from "../hooks/useHttpApi";
import { type ApiChannel, useChannels } from "../hooks/useChannels";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";

interface Props {
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  addLog: (dir: LogEntry["dir"], text: string) => void;
}

export default function ChannelsPanel({ wsUrl, token, wsState, addLog }: Props) {
  const httpUrl = deriveHttpUrl(wsUrl);
  const api = useChannels(httpUrl, token);

  const [channels, setChannels] = useState<ApiChannel[]>([]);
  const [error, setError] = useState("");

  // Add channel form
  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");

  // Token display modal
  const [tokenModal, setTokenModal] = useState<{ token: string; label: string } | null>(null);

  // Confirm delete
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Confirm rotate
  const [confirmRotate, setConfirmRotate] = useState<string | null>(null);

  const refreshChannels = useCallback(async () => {
    setError("");
    try {
      const list = await api.listChannels();
      setChannels(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [api]);

  useEffect(() => {
    if (wsState !== "disconnected") {
      refreshChannels();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsState]);

  const handleCreate = async () => {
    setError("");
    try {
      const ch = await api.createChannel(newName.trim(), "openclaw");
      addLog("sys", `Channel created: ${ch.name}`);
      setTokenModal({ token: ch.token!, label: `Token for "${ch.name}"` });
      setNewName("");
      setShowAddForm(false);
      await refreshChannels();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addLog("sys", `Channel create error: ${msg}`);
    }
  };

  const handleActivate = async (id: string) => {
    setError("");
    try {
      await api.activateChannel(id);
      addLog("sys", `Channel activated: ${id}`);
      await refreshChannels();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  const handleDelete = async (id: string) => {
    setError("");
    setConfirmDelete(null);
    try {
      await api.deleteChannel(id);
      addLog("sys", `Channel deleted: ${id}`);
      await refreshChannels();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  const handleRotate = async (id: string) => {
    setError("");
    setConfirmRotate(null);
    try {
      const newToken = await api.rotateToken(id);
      addLog("sys", `Token rotated for channel: ${id}`);
      setTokenModal({ token: newToken, label: "New token (rotated)" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  if (wsState === "disconnected") return null;

  const btnClass =
    "rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-700";
  const btnSmClass =
    "rounded px-2 py-1 text-xs font-medium";
  const errorClass = "text-xs text-red-400 mt-1";

  return (
    <div className="rounded border border-gray-700 bg-gray-900 text-sm">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-700 px-4 py-2">
        <span className="font-semibold text-gray-200">Channels</span>
        <div className="flex gap-2">
          <button
            className="text-xs text-gray-400 hover:text-white"
            onClick={refreshChannels}
          >
            Refresh &#8635;
          </button>
          <button
            className="text-xs text-indigo-400 hover:text-indigo-300"
            onClick={() => setShowAddForm(!showAddForm)}
          >
            {showAddForm ? "Cancel" : "+ Add Channel"}
          </button>
        </div>
      </div>

      {/* Add Channel Form */}
      {showAddForm && (
        <div className="border-b border-gray-700 px-4 py-3">
          <div className="flex items-end gap-2">
            <label className="flex flex-1 flex-col gap-1 text-xs text-gray-400">
              Name
              <input
                className="rounded bg-gray-800 px-3 py-1.5 text-sm text-white outline-none focus:ring-1 focus:ring-indigo-500"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Home OpenClaw"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-gray-400">
              Type
              <select
                className="rounded bg-gray-800 px-3 py-1.5 text-sm text-white outline-none focus:ring-1 focus:ring-indigo-500"
                disabled
              >
                <option value="openclaw">openclaw</option>
              </select>
            </label>
            <button
              className={btnClass}
              onClick={handleCreate}
              disabled={!newName.trim()}
            >
              Create
            </button>
          </div>
        </div>
      )}

      {/* Channel List */}
      <div className="px-4 py-3">
        {error && <p className={errorClass}>{error}</p>}
        {channels.length === 0 && !error && (
          <p className="text-xs text-gray-500">No channels</p>
        )}
        <ul className="space-y-2">
          {channels.map((ch) => (
            <li
              key={ch.id}
              className={`flex items-center gap-2 rounded px-2 py-1.5 ${ch.active ? "bg-gray-800 ring-1 ring-indigo-500/50" : ""}`}
            >
              <span
                className={`inline-block h-2 w-2 rounded-full ${ch.active ? "bg-green-500" : "bg-gray-500"}`}
              />
              <span className="flex-1 text-gray-300">
                {ch.name}
                {ch.builtin && (
                  <span className="ml-2 rounded bg-orange-600/20 px-1.5 py-0.5 text-[10px] uppercase text-orange-400/80">
                    Limited features
                  </span>
                )}
                {ch.active && (
                  <span className="ml-2 rounded bg-indigo-600/30 px-1.5 py-0.5 text-[10px] uppercase text-indigo-300">
                    Active
                  </span>
                )}
              </span>
              <span className="text-xs text-gray-500">{ch.type}</span>

              {!ch.active && (
                <button
                  className={`${btnSmClass} bg-indigo-600 text-white hover:bg-indigo-700`}
                  onClick={() => handleActivate(ch.id)}
                >
                  Activate
                </button>
              )}

              {!ch.builtin && (
                <>
                  <button
                    className={`${btnSmClass} bg-gray-700 text-gray-300 hover:bg-gray-600`}
                    onClick={() => setConfirmRotate(ch.id)}
                  >
                    Rotate Token
                  </button>
                  <button
                    className={`${btnSmClass} bg-red-800 text-red-200 hover:bg-red-700`}
                    onClick={() => setConfirmDelete(ch.id)}
                  >
                    Delete
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>

      {/* Confirm Delete Dialog */}
      {confirmDelete && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/50 z-50">
          <div className="rounded-lg bg-gray-800 p-6 shadow-xl max-w-sm">
            <p className="text-sm text-gray-200 mb-4">Delete this channel? This cannot be undone.</p>
            <div className="flex gap-2 justify-end">
              <button
                className="rounded bg-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-600"
                onClick={() => setConfirmDelete(null)}
              >
                Cancel
              </button>
              <button
                className="rounded bg-red-600 px-3 py-1.5 text-sm text-white hover:bg-red-700"
                onClick={() => handleDelete(confirmDelete)}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm Rotate Dialog */}
      {confirmRotate && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/50 z-50">
          <div className="rounded-lg bg-gray-800 p-6 shadow-xl max-w-sm">
            <p className="text-sm text-gray-200 mb-4">
              Rotate this channel's token? The old token will stop working immediately.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                className="rounded bg-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-600"
                onClick={() => setConfirmRotate(null)}
              >
                Cancel
              </button>
              <button
                className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700"
                onClick={() => handleRotate(confirmRotate)}
              >
                Rotate
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Token Display Modal */}
      {tokenModal && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/50 z-50">
          <div className="rounded-lg bg-gray-800 p-6 shadow-xl max-w-lg">
            <p className="text-sm font-semibold text-gray-200 mb-2">{tokenModal.label}</p>
            <p className="text-xs text-yellow-400 mb-3">
              This token will not be shown again. Copy it now.
            </p>
            <div className="rounded bg-gray-900 p-3 font-mono text-xs text-green-400 break-all select-all mb-4">
              {tokenModal.token}
            </div>
            <div className="flex gap-2 justify-end">
              <button
                className="rounded bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700"
                onClick={() => {
                  navigator.clipboard.writeText(tokenModal.token).catch(() => {});
                }}
              >
                Copy
              </button>
              <button
                className="rounded bg-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-600"
                onClick={() => setTokenModal(null)}
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
