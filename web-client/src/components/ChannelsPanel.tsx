import { useCallback, useEffect, useState } from "react";
import { deriveHttpUrl } from "../hooks/useHttpApi";
import { type ApiChannel, useChannels } from "../hooks/useChannels";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import Icon from "./Icon";

interface Props {
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  addLog: (dir: LogEntry["dir"], text: string) => void;
}

const inputClass =
  "rounded-lg border border-white/5 bg-zinc-900/80 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-500/40 focus:ring-2 focus:ring-indigo-500/30";

const primaryBtn =
  "focus-ring rounded-lg bg-indigo-500 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-400 disabled:cursor-not-allowed disabled:opacity-50";

const ghostBtn =
  "focus-ring inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-zinc-400 hover:bg-white/5 hover:text-zinc-200";

const dangerBtn =
  "focus-ring inline-flex items-center gap-1.5 rounded-md bg-red-500/10 px-2.5 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20";

const subtleBtn =
  "focus-ring inline-flex items-center gap-1.5 rounded-md bg-white/5 px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-white/10";

export default function ChannelsPanel({ wsUrl, token, wsState, addLog }: Props) {
  const httpUrl = deriveHttpUrl(wsUrl);
  const api = useChannels(httpUrl, token);

  const [channels, setChannels] = useState<ApiChannel[]>([]);
  const [error, setError] = useState("");

  const [showAddForm, setShowAddForm] = useState(false);
  const [newName, setNewName] = useState("");

  const [tokenModal, setTokenModal] = useState<
    { token: string; label: string } | null
  >(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
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

  if (wsState === "disconnected") {
    return (
      <div className="card p-6 text-sm text-zinc-500">
        Connect to a server to manage channels.
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">Channels</h2>
          <p className="text-xs text-zinc-500">
            One channel routes voice traffic at a time.
          </p>
        </div>
        <div className="flex gap-1">
          <button className={ghostBtn} onClick={refreshChannels}>
            <Icon name="refresh" size={14} />
            Refresh
          </button>
          <button
            className={ghostBtn + " text-indigo-300 hover:text-indigo-200"}
            onClick={() => setShowAddForm(!showAddForm)}
          >
            <Icon name="plus" size={14} />
            {showAddForm ? "Cancel" : "Add channel"}
          </button>
        </div>
      </div>

      {showAddForm && (
        <div className="border-b border-white/5 px-5 py-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-1 flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Name
              <input
                className={inputClass}
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="e.g. Home OpenClaw"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Type
              <select className={inputClass} disabled>
                <option value="openclaw">openclaw</option>
              </select>
            </label>
            <button
              className={primaryBtn}
              onClick={handleCreate}
              disabled={!newName.trim()}
            >
              Create
            </button>
          </div>
        </div>
      )}

      <div className="px-5 py-4">
        {error && (
          <p className="mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}
        {channels.length === 0 && !error && (
          <p className="text-xs text-zinc-500">No channels.</p>
        )}
        <ul className="space-y-2">
          {channels.map((ch) => (
            <li
              key={ch.id}
              className={`flex flex-wrap items-center gap-3 rounded-lg border border-white/5 bg-zinc-900/40 px-3 py-2.5 ${
                ch.active ? "ring-1 ring-indigo-500/40" : ""
              }`}
            >
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  ch.active
                    ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]"
                    : "bg-zinc-600"
                }`}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium text-zinc-200">
                    {ch.name}
                  </span>
                  {ch.builtin && (
                    <span className="pill border-amber-500/30 bg-amber-500/10 text-amber-300">
                      Limited
                    </span>
                  )}
                  {ch.active && (
                    <span className="pill border-indigo-500/30 bg-indigo-500/10 text-indigo-300">
                      Active
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-zinc-500">
                  {ch.type}
                  {ch.id ? ` · ${ch.id}` : ""}
                </div>
              </div>

              {!ch.active && (
                <button className={primaryBtn} onClick={() => handleActivate(ch.id)}>
                  Activate
                </button>
              )}

              {!ch.builtin && (
                <>
                  <button
                    className={subtleBtn}
                    onClick={() => setConfirmRotate(ch.id)}
                  >
                    <Icon name="rotate-key" size={13} />
                    Rotate token
                  </button>
                  <button
                    className={dangerBtn}
                    onClick={() => setConfirmDelete(ch.id)}
                  >
                    <Icon name="trash" size={13} />
                    Delete
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>

      {confirmDelete && (
        <Dialog
          message="Delete this channel? This cannot be undone."
          confirmLabel="Delete"
          confirmTone="danger"
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => handleDelete(confirmDelete)}
        />
      )}
      {confirmRotate && (
        <Dialog
          message="Rotate this channel's token? The old token will stop working immediately."
          confirmLabel="Rotate"
          confirmTone="primary"
          onCancel={() => setConfirmRotate(null)}
          onConfirm={() => handleRotate(confirmRotate)}
        />
      )}
      {tokenModal && (
        <TokenModal
          label={tokenModal.label}
          token={tokenModal.token}
          onClose={() => setTokenModal(null)}
        />
      )}
    </div>
  );
}

interface DialogProps {
  message: string;
  confirmLabel: string;
  confirmTone: "primary" | "danger";
  onCancel: () => void;
  onConfirm: () => void;
}

function Dialog({
  message,
  confirmLabel,
  confirmTone,
  onCancel,
  onConfirm,
}: DialogProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="card max-w-sm p-6 shadow-2xl">
        <p className="mb-5 text-sm text-zinc-200">{message}</p>
        <div className="flex justify-end gap-2">
          <button className={subtleBtn} onClick={onCancel}>
            Cancel
          </button>
          <button
            className={
              confirmTone === "danger"
                ? "focus-ring rounded-md bg-red-500 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-400"
                : primaryBtn
            }
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function TokenModal({
  label,
  token,
  onClose,
}: {
  label: string;
  token: string;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="card w-full max-w-lg p-6 shadow-2xl">
        <p className="mb-1 text-sm font-semibold text-zinc-100">{label}</p>
        <p className="mb-3 text-xs text-amber-300">
          This token will not be shown again. Copy it now.
        </p>
        <div className="mb-4 select-all break-all rounded-lg border border-white/5 bg-black/40 p-3 font-mono text-xs text-emerald-300">
          {token}
        </div>
        <div className="flex justify-end gap-2">
          <button
            className={subtleBtn}
            onClick={() => {
              navigator.clipboard.writeText(token).catch(() => {});
            }}
          >
            <Icon name="copy" size={13} />
            Copy
          </button>
          <button className={primaryBtn} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
