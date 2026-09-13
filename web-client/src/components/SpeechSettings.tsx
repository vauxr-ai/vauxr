import { ownerFetch } from "../auth/api";
import { useCallback, useEffect, useState } from "react";

type Backend = { id: string; kind: "stt" | "tts"; model: string; voices: string[]; readiness: string };
type Defaults = { stt_backend: string; tts_backend: string; voices: Record<string, string> };
type View = { defaults: Defaults; overrides: Partial<Defaults>; effective: {
  stt_backend: string; tts_backend: string; voice_id: string;
} | null; backends: Backend[]; error: string | null };

export default function SpeechSettings({ deviceId }: {
  deviceId?: string;
}) {
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const path = deviceId ? `/api/devices/${encodeURIComponent(deviceId)}/speech` : "/api/speech";
  const request = useCallback(async (patch?: object, signal?: AbortSignal) => {
    const response = await ownerFetch(path, {
      method: patch ? "PATCH" : "GET", signal,
      headers: { "Content-Type": "application/json" },
      ...(patch ? { body: JSON.stringify(patch) } : {}),
    });
    if (!response.ok) throw new Error(`Speech settings request failed (${response.status})`);
    const body = await response.json() as View;
    if (!body || !body.defaults || !body.overrides || !Array.isArray(body.backends)) {
      throw new Error("Invalid speech settings response");
    }
    return body;
  }, [path]);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    request(undefined, controller.signal).then(setData).catch((err: Error) => {
      if (!controller.signal.aborted) setError(err.message);
    });
    return () => controller.abort();
  }, [request]);
  async function update(patch?: object) {
    setBusy(true);
    setError("");
    try { setData(await request(patch)); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  }
  const effective = data?.effective;
  const selectedTts = effective && data?.backends.find(b => b.id === effective.tts_backend);
  const style = "rounded border border-white/10 bg-zinc-900 p-2 text-sm";
  return <section aria-label={deviceId ? "Device speech" : "Global speech"} className="card space-y-3 p-4">
    <h2 className="font-semibold">{deviceId ? "Device speech" : "Global speech defaults"}</h2>
    <p className="text-xs text-zinc-400">Changes apply to the next turn. Voices belong to a configured model.</p>
    {error && <p role="alert">{error}</p>}
    {data?.error && <p role="alert">{data.error}</p>}
    {data && <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
      {(["stt", "tts"] as const).map(kind => {
        const key = `${kind}_backend` as const;
        return <label key={kind} className="flex flex-col gap-1">{kind.toUpperCase()} backend
          <select className={style} value={deviceId ? data.overrides[key] || "" : data.defaults[key]}
            onChange={e => void update({ [key]: e.target.value || null })}>
            {deviceId && <option value="">Inherit global ({data.defaults[key]})</option>}
            {data.backends.filter(b => b.kind === kind).map(b =>
              <option key={b.id} value={b.id}>{b.id} · {b.model} · {b.readiness}</option>)}
          </select>
        </label>;
      })}
      {selectedTts && <label className="flex flex-col gap-1">Voice
        <select className={style}
          value={deviceId ? data.overrides.voices?.[selectedTts.id] || "" : data.defaults.voices[selectedTts.id] || selectedTts.voices[0]}
          onChange={e => void update({ voices: { [selectedTts.id]: e.target.value || null } })}>
          {deviceId && <option value="">Inherit model default ({data.defaults.voices[selectedTts.id] || selectedTts.voices[0]})</option>}
          {selectedTts.voices.map(v => <option key={v} value={v}>{v}</option>)}
        </select>
      </label>}
      {deviceId && <button className={style} onClick={() => void update({ stt_backend: null, tts_backend: null, voices: null })}>Reset to defaults</button>}
      <button className={style} onClick={() => void update()}>Refresh speech</button>
    </fieldset>}
    {effective && <p className="text-sm">Effective: {effective.stt_backend} / {effective.tts_backend} / {effective.voice_id}</p>}
    <p className="text-xs text-zinc-500">Readiness checks Wyoming capabilities, not model inference. Unavailable providers do not fall back automatically.</p>
  </section>;
}
