import PluginsPanel from "./PluginsPanel";
import AccessPanel from "./AccessPanel";
import SpeechSettings from "./SpeechSettings";
import { useCallback, useEffect, useRef, useState } from "react";
import { deriveHttpUrl } from "../hooks/useHttpApi";
import { type ApiWebhook, useWebhooks } from "../hooks/useWebhooks";
import type { ConnectionState, LogEntry } from "../hooks/useWebSocket";
import Icon from "./Icon";

interface Props {
  wsUrl: string;
  token: string;
  wsState: ConnectionState;
  onDirtyChange?: (dirty: boolean) => void;
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

function AuthInput({
  value,
  onChange,
  placeholder,
  "aria-label": ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  "aria-label"?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative w-full">
      <input
        type={visible ? "text" : "password"}
        className={inputClass + " w-full pr-10"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={ariaLabel}
      />
      <button
        type="button"
        className="focus-ring absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-zinc-500 hover:text-zinc-300"
        aria-label={visible ? "Hide authorization" : "Show authorization"}
        onClick={() => setVisible((v) => !v)}
      >
        <Icon name={visible ? "eye-off" : "eye"} size={16} />
      </button>
    </div>
  );
}

function parseBodyField(text: string): { body?: Record<string, unknown> | null; error?: string } {
  const trimmed = text.trim();
  if (!trimmed) return { body: null };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { error: "Body must be a JSON object" };
    }
    return { body: parsed as Record<string, unknown> };
  } catch {
    return { error: "Body must be valid JSON" };
  }
}

export default function SettingsPanel({ wsUrl, token, wsState: _wsState, addLog, onDirtyChange }: Props) {
  const httpUrl = deriveHttpUrl(wsUrl);
  const api = useWebhooks(httpUrl, token);

  const [hooks, setHooks] = useState<ApiWebhook[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  const [saved, setSaved] = useState("");
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [editAuth, setEditAuth] = useState("");
  const [editBody, setEditBody] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      setHooks(await api.listWebhooks(true));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [api.listWebhooks]);

  useEffect(() => { void refresh(); }, [refresh]);
  const dirty = showAdd || editing !== null;
  useEffect(() => { if (saved && !busy) addButton.current?.focus(); }, [saved, busy]);
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const discard = () => !dirty || window.confirm("Discard unsaved webhook edits?");
  async function mutate(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setSaved("");
    try { await action(); } finally { setBusy(false); }
  }

  const handleCreate = async () => {
    setError("");
    const parsed = parseBodyField(bodyText);
    if (parsed.error) {
      setError(parsed.error);
      return;
    }
    try {
      const created = await api.createWebhook(
        name.trim(),
        url.trim(),
        authorization.trim(),
        parsed.body ?? undefined,
      );
      addLog("sys", `Webhook created: ${created.name}`);
      setName("");
      setUrl("");
      setAuthorization("");
      setBodyText("");
      setShowAdd(false);
      setSaved("Webhook change saved.");
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`${msg} The outcome may be unknown. Refresh and check the saved webhook before another change.`);
      addLog("sys", `Webhook create error: ${msg}`);
    }
  };

  const handleSaveEdit = async (id: string) => {
    setError("");
    const parsed = parseBodyField(editBody);
    if (parsed.error) {
      setError(parsed.error);
      return;
    }
    try {
      const patch: {
        name: string;
        url: string;
        authorization?: string;
        body: Record<string, unknown> | null;
      } = {
        name: editName.trim(),
        url: editUrl.trim(),
        body: parsed.body ?? null,
      };
      if (editAuth.trim()) patch.authorization = editAuth.trim();
      await api.updateWebhook(id, patch);
      addLog("sys", `Webhook updated: ${editName.trim()}`);
      setEditing(null);
      setEditAuth("");
      setSaved("Webhook change saved.");
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`${msg} The outcome may be unknown. Refresh and check the saved webhook before another change.`);
    }
  };

  const handleDuplicate = async (id: string) => {
    setError("");
    try {
      const created = await api.duplicateWebhook(id);
      addLog("sys", `Webhook duplicated: ${created.name}`);
      setSaved("Webhook change saved.");
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`${msg} The outcome may be unknown. Refresh and check the saved webhook before another change.`);
      addLog("sys", `Webhook duplicate error: ${msg}`);
    }
  };

  const handleDelete = async (id: string) => {
    setError("");
    setConfirmDelete(null);
    try {
      await api.deleteWebhook(id);
      addLog("sys", `Webhook deleted: ${id}`);
      setSaved("Webhook change saved.");
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`${msg} The outcome may be unknown. Refresh and check the saved webhook before another change.`);
    }
  };

  return (
    <div className="administration space-y-4">
      <nav aria-label="Settings sections" className="settings-sections">
        <a href="#voice-defaults">Voice providers/defaults</a>
        <a href="#plugins">Plugins</a>
        <a href="#pairing-access">Pairing &amp; Access</a>
        <a href="#webhooks">Webhooks</a>
      </nav>
      <section id="voice-defaults" tabIndex={-1} aria-label="Voice providers and defaults">
        <h2>Voice providers/defaults</h2>
        <p>Choose from server-configured providers. Add providers and credentials in server configuration; this page does not install plugins or edit provider credentials.</p>
        <SpeechSettings />
      </section>
      <PluginsPanel />
      <section id="pairing-access" tabIndex={-1} aria-label="Pairing and access"><AccessPanel /></section>
    <section id="webhooks" tabIndex={-1} aria-label="Webhooks" className="card overflow-hidden">
      <div className="flex flex-wrap gap-3 items-center justify-between border-b border-white/5 px-5 py-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-100">Webhooks</h2>
          <p className="text-xs text-zinc-500">
            Named endpoints for action-button gestures. Pick one per device in Devices.
          </p>
        </div>
        <div className="flex gap-1">
          <button className={ghostBtn} disabled={busy || loading} onClick={() => void refresh()} type="button">
            <Icon name="refresh" size={14} />
            Refresh
          </button>
          <button
            className={ghostBtn + " text-indigo-300 hover:text-indigo-200"}
            ref={addButton}
            disabled={busy} onClick={() => { if (discard()) {
              setShowAdd(!showAdd); setEditing(null); setSaved("");
              setName(""); setUrl(""); setAuthorization(""); setBodyText(""); setEditAuth("");
            } }}
            type="button"
          >
            <Icon name="plus" size={14} />
            {showAdd ? "Cancel" : "Add webhook"}
          </button>
        </div>
      </div>

      {showAdd && (
        <fieldset disabled={busy} className="space-y-3 border-b border-white/5 px-5 py-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-0 w-full sm:w-auto sm:min-w-[140px] flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Name
              <input
                className={inputClass}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Home Assistant"
              />
            </label>
            <label className="flex min-w-0 w-full sm:min-w-[240px] flex-1 flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              URL
              <input
                className={inputClass}
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://homeassistant.local:8123/api/services/scene/turn_on"
              />
            </label>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex min-w-0 w-full sm:min-w-[240px] flex-1 flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Authorization header
              <AuthInput
                value={authorization}
                onChange={setAuthorization}
                placeholder="Bearer eyJ… (optional)"
              />
            </label>
          </div>
          <label className="flex flex-col gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
            JSON body
            <textarea
              className={inputClass + " min-h-[72px] font-mono text-xs"}
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              placeholder='{"entity_id":"scene.living_room"}'
              spellCheck={false}
            />
          </label>
          <button
            className={primaryBtn}
            onClick={() => void mutate(handleCreate)}
            disabled={busy || !name.trim() || !url.trim()}
            type="button"
          >
            Create
          </button>
        </fieldset>
      )}

      <div className="px-5 py-4">
        {dirty && <p role="status">Unsaved webhook edits. Save or cancel before leaving Settings.</p>}
        {busy && <p role="status">Saving webhook change…</p>}
        {saved && <p role="status">{saved}</p>}
        {error && (
          <p role="alert" className="mb-3 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </p>
        )}
        {loading ? <p role="status">Loading webhooks…</p> : hooks.length === 0 && !error ? (
          <p className="text-sm text-zinc-500">No webhooks yet.</p>
        ) : (
          <ul className="space-y-2">
            {hooks.map((hook) => (
              <li
                key={hook.id}
                className="rounded-lg border border-white/5 bg-zinc-900/40 px-3 py-3"
              >
                {editing === hook.id ? (
                  <fieldset disabled={busy} className="space-y-2">
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
                    <AuthInput
                      value={editAuth}
                      onChange={setEditAuth}
                      placeholder={
                        hook.has_authorization
                          ? "Leave blank to keep existing authorization"
                          : "Authorization header (optional)"
                      }
                      aria-label="Authorization header"
                    />
                    <textarea
                      className={inputClass + " min-h-[72px] w-full font-mono text-xs"}
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      placeholder='{"entity_id":"scene.living_room"}'
                      aria-label="JSON body"
                      spellCheck={false}
                    />
                    <div className="flex gap-2">
                      <button className={primaryBtn} type="button" disabled={busy || !editName.trim() || !editUrl.trim()} onClick={() => void mutate(() => handleSaveEdit(hook.id))}>
                        Save
                      </button>
                      <button className={ghostBtn} type="button" disabled={busy} onClick={() => { if (discard()) { setEditing(null); setEditAuth(""); } }}>
                        Cancel
                      </button>
                    </div>
                  </fieldset>
                ) : (
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-zinc-200">{hook.name}</div>
                      <div className="truncate font-mono text-[11px] text-zinc-500">{hook.url}</div>
                      {hook.body && (
                        <div className="mt-1 truncate font-mono text-[11px] text-zinc-600">
                          {JSON.stringify(hook.body)}
                        </div>
                      )}
                      {hook.has_authorization && (
                        <div className="mt-1 text-[10px] uppercase tracking-wider text-zinc-600">
                          Authorization set
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-wrap gap-1">
                      <button
                        className={ghostBtn}
                        type="button"
                        disabled={busy}
                        onClick={() => {
                          if (!discard()) return;
                          setSaved("");
                          setShowAdd(false);
                          setEditing(hook.id);
                          setEditName(hook.name);
                          setEditUrl(hook.url);
                          setEditAuth("");
                          setEditBody(hook.body ? JSON.stringify(hook.body, null, 2) : "");
                        }}
                      >
                        Edit
                      </button>
                      <button
                        className={ghostBtn}
                        type="button"
                        disabled={busy || dirty} onClick={() => void mutate(() => handleDuplicate(hook.id))}
                      >
                        Duplicate
                      </button>
                      {confirmDelete === hook.id ? (
                        <>
                          <button className={dangerBtn} type="button" disabled={busy} onClick={() => void mutate(() => handleDelete(hook.id))}>
                            Confirm
                          </button>
                          <button className={ghostBtn} type="button" onClick={() => setConfirmDelete(null)}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button className={dangerBtn} type="button" disabled={busy || dirty} onClick={() => setConfirmDelete(hook.id)}>
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
    </section>
    </div>
  );
}
