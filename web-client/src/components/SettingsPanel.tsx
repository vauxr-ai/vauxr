import { useCallback, useEffect, useState } from "react";
import { deriveHttpUrl } from "../hooks/useHttpApi";
import { type ApiWebhook, useWebhooks } from "../hooks/useWebhooks";
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

export default function SettingsPanel({ wsUrl, token, wsState, addLog }: Props) {
  const httpUrl = deriveHttpUrl(wsUrl);
  const api = useWebhooks(httpUrl, token);

  const [hooks, setHooks] = useState<ApiWebhook[]>([]);
  const [error, setError] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [editAuth, setEditAuth] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError("");
    try {
      setHooks(await api.listWebhooks());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [api]);

  useEffect(() => {
    if (wsState !== "disconnected") {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wsState]);

  const handleCreate = async () => {
    setError("");
    try {
      const created = await api.createWebhook(name.trim(), url.trim(), authorization.trim());
      addLog("sys", `Webhook created: ${created.name}`);
      setName("");
      setUrl("");
      setAuthorization("");
      setShowAdd(false);
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      addLog("sys", `Webhook create error: ${msg}`);
    }
  };

  const handleSaveEdit = async (id: string) => {
    setError("");
    try {
      const patch: { name: string; url: string; authorization?: string } = {
        name: editName.trim(),
        url: editUrl.trim(),
      };
      if (editAuth.trim()) patch.authorization = editAuth.trim();
      await api.updateWebhook(id, patch);
      addLog("sys", `Webhook updated: ${editName.trim()}`);
      setEditing(null);
      setEditAuth("");
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  const handleDelete = async (id: string) => {
    setError("");
    setConfirmDelete(null);
    try {
      await api.deleteWebhook(id);
      addLog("sys", `Webhook deleted: ${id}`);
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    }
  };

  if (wsState === "disconnected") {
    return (
      <div className="card p-6 text-sm text-zinc-500">
        Connect to a server to manage webhooks.
      </div>
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">Webhooks</h2>
          <p className="text-xs text-zinc-500">
            Named endpoints for action-button gestures. Pick one per device in Devices.
          </p>
        </div>
        <div className="flex gap-1">
          <button className={ghostBtn} onClick={refresh} type="button">
            <Icon name="refresh" size={14} />
            Refresh
          </button>
          <button
            className={ghostBtn + " text-indigo-300 hover:text-indigo-200"}
            onClick={() => setShowAdd(!showAdd)}
            type="button"
          >
            <Icon name="plus" size={14} />
            {showAdd ? "Cancel" : "Add webhook"}
          </button>
        </div>
      </div>

      {showAdd && (
        <div className="space-y-3 border-b border-white/5 px-5 py-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-[140px] flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Name
              <input
                className={inputClass}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Home Assistant"
              />
            </label>
            <label className="flex min-w-[240px] flex-1 flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              URL
              <input
                className={inputClass}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="http://homeassistant.local:8123/api/events/vauxr.button_press"
              />
            </label>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-[240px] flex-1 flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Authorization header
              <input
                type="password"
                className={inputClass}
                value={authorization}
                onChange={(e) => setAuthorization(e.target.value)}
                placeholder="Bearer eyJ… (optional)"
              />
            </label>
            <button
              className={primaryBtn}
              onClick={handleCreate}
              disabled={!name.trim() || !url.trim()}
              type="button"
            >
              Create
            </button>
          </div>
        </div>
      )}

      <div className="px-5 py-4">
        {error && (
          <p className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}
        {hooks.length === 0 ? (
          <p className="text-sm text-zinc-500">No webhooks yet.</p>
        ) : (
          <ul className="space-y-2">
            {hooks.map((hook) => (
              <li
                key={hook.id}
                className="rounded-lg border border-white/5 bg-zinc-900/40 px-3 py-3"
              >
                {editing === hook.id ? (
                  <div className="space-y-2">
                    <input
                      className={inputClass + " w-full"}
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      aria-label="Webhook name"
                    />
                    <input
                      className={inputClass + " w-full"}
                      value={editUrl}
                      onChange={(e) => setEditUrl(e.target.value)}
                      aria-label="Webhook URL"
                    />
                    <input
                      type="password"
                      className={inputClass + " w-full"}
                      value={editAuth}
                      onChange={(e) => setEditAuth(e.target.value)}
                      placeholder={
                        hook.has_authorization
                          ? "Leave blank to keep existing authorization"
                          : "Authorization header (optional)"
                      }
                      aria-label="Authorization header"
                    />
                    <div className="flex gap-2">
                      <button className={primaryBtn} type="button" onClick={() => handleSaveEdit(hook.id)}>
                        Save
                      </button>
                      <button className={ghostBtn} type="button" onClick={() => setEditing(null)}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-zinc-200">{hook.name}</div>
                      <div className="truncate font-mono text-[11px] text-zinc-500">{hook.url}</div>
                      {hook.has_authorization && (
                        <div className="mt-1 text-[10px] uppercase tracking-wider text-zinc-600">
                          Authorization set
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <button
                        className={ghostBtn}
                        type="button"
                        onClick={() => {
                          setEditing(hook.id);
                          setEditName(hook.name);
                          setEditUrl(hook.url);
                          setEditAuth("");
                        }}
                      >
                        Edit
                      </button>
                      {confirmDelete === hook.id ? (
                        <>
                          <button className={dangerBtn} type="button" onClick={() => handleDelete(hook.id)}>
                            Confirm
                          </button>
                          <button className={ghostBtn} type="button" onClick={() => setConfirmDelete(null)}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button className={dangerBtn} type="button" onClick={() => setConfirmDelete(hook.id)}>
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
