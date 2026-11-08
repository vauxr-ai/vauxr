import { ownerFetch } from "../auth/api";
import { useCallback, useEffect, useId, useState } from "react";

type Backend = {
  id: string;
  kind: "stt" | "tts" | "realtime";
  model: string;
  voices: string[];
  readiness: string;
};
type Defaults = {
  mode?: "standard" | "realtime";
  realtime_backend?: string;
  realtime_voice?: string;
  stt_backend: string;
  tts_backend: string;
  voices: Record<string, string>;
};
type View = {
  voice?: {
    mode: "standard" | "realtime";
    realtime_backend: string;
    realtime_model: string;
    realtime_voice: string;
  };
  realtime?: { configured: boolean; voices: string[] };
  defaults: Defaults;
  overrides: Partial<Defaults>;
  effective: {
    stt_backend: string;
    tts_backend: string;
    voice_id: string;
  } | null;
  backends: Backend[];
  error: string | null;
};

type SpeechOption = { value: string; label: string };

function SpeechSelect({
  label,
  value,
  options,
  onChange,
  describeSelection = true,
}: {
  label: string;
  value: string;
  options: SpeechOption[];
  onChange: (value: string) => void;
  describeSelection?: boolean;
}) {
  const id = useId();
  const descriptionId = `${id}-selection`;
  const selected = options.find((option) => option.value === value);
  return (
    <div className="speech-field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="rounded border border-white/10 bg-zinc-900 p-2 text-sm"
        value={value}
        aria-describedby={
          describeSelection && selected ? descriptionId : undefined
        }
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {describeSelection && selected && (
        <p
          id={descriptionId}
          className="speech-selection text-xs text-zinc-400"
        >
          Selected: {selected.label}
        </p>
      )}
    </div>
  );
}

export default function SpeechSettings({
  deviceId,
  onModeChange,
}: {
  deviceId?: string;
  onModeChange?: (mode: "standard" | "realtime") => void;
}) {
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const path = deviceId
    ? `/api/devices/${encodeURIComponent(deviceId)}/speech`
    : "/api/speech";
  const request = useCallback(
    async (patch?: object, signal?: AbortSignal) => {
      const response = await ownerFetch(path, {
        method: patch ? "PATCH" : "GET",
        signal,
        headers: { "Content-Type": "application/json" },
        ...(patch ? { body: JSON.stringify(patch) } : {}),
      });
      if (!response.ok)
        throw new Error(`Speech settings request failed (${response.status})`);
      const body = (await response.json()) as View;
      if (
        !body ||
        !body.defaults ||
        !body.overrides ||
        !Array.isArray(body.backends)
      ) {
        throw new Error("Invalid speech settings response");
      }
      return body;
    },
    [path],
  );
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    request(undefined, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (!controller.signal.aborted) setError(err.message);
      });
    const changed = (event: Event) => {
      const detail = (event as CustomEvent).detail;
      if (detail?.source === "talk" && detail.deviceId === deviceId) {
        request(undefined, controller.signal)
          .then(setData)
          .catch((err: Error) => {
            if (!controller.signal.aborted) setError(err.message);
          });
      }
    };
    window.addEventListener("speech-settings-changed", changed);
    return () => {
      controller.abort();
      window.removeEventListener("speech-settings-changed", changed);
    };
  }, [request, deviceId]);
  async function update(patch?: object) {
    setBusy(true);
    setError("");
    try {
      setData(await request(patch));
      window.dispatchEvent(
        new CustomEvent("speech-settings-changed", {
          detail: {
            source: "settings",
            scope: deviceId ? "device" : "global",
            deviceId,
          },
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (data) onModeChange?.(data.voice?.mode || "standard");
  }, [data, onModeChange]);
  const effective = data?.effective;
  const selectedTts =
    effective && data?.backends.find((b) => b.id === effective.tts_backend);
  const style = "rounded border border-white/10 bg-zinc-900 p-2 text-sm";
  const backendOptions = (kind: Backend["kind"]): SpeechOption[] =>
    (data?.backends || [])
      .filter((backend) => backend.kind === kind)
      .map((backend) => ({
        value: backend.id,
        label: `${backend.id} · ${backend.model} · ${backend.readiness}`,
      }));
  return (
    <section
      aria-label={deviceId ? "Device speech" : "Global speech"}
      className="speech-settings card space-y-3 p-4"
    >
      <h2 className="font-semibold">
        {deviceId ? "Device speech" : "Global speech defaults"}
      </h2>
      <p className="text-xs text-zinc-400">
        Changes apply to the next turn. Voices belong to a configured model.
      </p>
      {error && <p role="alert">{error}</p>}
      {data?.error && <p role="alert">{data.error}</p>}
      {data && (
        <fieldset disabled={busy} className="speech-fields">
          <SpeechSelect
            label="Voice mode"
            value={data.voice?.mode || "standard"}
            describeSelection={false}
            options={[
              { value: "standard", label: "Standard" },
              { value: "realtime", label: "Realtime" },
            ]}
            onChange={(mode) => void update({ mode })}
          />
          {data.voice?.mode === "realtime" ? (
            <>
              <SpeechSelect
                label="Realtime provider / model"
                value={data.voice.realtime_backend}
                options={backendOptions("realtime")}
                onChange={(realtime_backend) =>
                  void update({ realtime_backend })
                }
              />
              <SpeechSelect
                label="Realtime voice"
                value={data.voice.realtime_voice}
                options={(data.realtime?.voices || []).map((voice) => ({
                  value: voice,
                  label: voice,
                }))}
                onChange={(realtime_voice) => void update({ realtime_voice })}
              />
              {!data.realtime?.configured && (
                <p className="speech-note">
                  Realtime needs server setup: enable Realtime and configure the
                  OpenAI API key on Vauxr.
                </p>
              )}
            </>
          ) : (
            <>
              {(["stt", "tts"] as const).map((kind) => {
                const key = `${kind}_backend` as const;
                const options = backendOptions(kind);
                if (deviceId)
                  options.unshift({
                    value: "",
                    label: `Inherit global (${data.defaults[key]})`,
                  });
                return (
                  <SpeechSelect
                    key={kind}
                    label={`${kind.toUpperCase()} backend`}
                    value={
                      deviceId ? data.overrides[key] || "" : data.defaults[key]
                    }
                    options={options}
                    onChange={(value) => void update({ [key]: value || null })}
                  />
                );
              })}
              {selectedTts && (
                <SpeechSelect
                  label="Voice"
                  value={
                    deviceId
                      ? data.overrides.voices?.[selectedTts.id] || ""
                      : data.defaults.voices[selectedTts.id] ||
                        selectedTts.voices[0]
                  }
                  options={[
                    ...(deviceId
                      ? [
                          {
                            value: "",
                            label: `Inherit model default (${data.defaults.voices[selectedTts.id] || selectedTts.voices[0]})`,
                          },
                        ]
                      : []),
                    ...selectedTts.voices.map((voice) => ({
                      value: voice,
                      label: voice,
                    })),
                  ]}
                  onChange={(value) =>
                    void update({ voices: { [selectedTts.id]: value || null } })
                  }
                />
              )}
            </>
          )}
          <div className="speech-actions">
            {deviceId && (
              <button
                className={style}
                onClick={() =>
                  void update({
                    stt_backend: null,
                    tts_backend: null,
                    voices: null,
                  })
                }
              >
                Reset to defaults
              </button>
            )}
            <button className={style} onClick={() => void update()}>
              Refresh speech
            </button>
          </div>
        </fieldset>
      )}
      {data?.voice?.mode !== "realtime" && effective && (
        <p className="text-sm">
          Effective: {effective.stt_backend} / {effective.tts_backend} /{" "}
          {effective.voice_id}
        </p>
      )}
      <p className="text-xs text-zinc-500">
        Readiness checks provider availability, not model inference. Unavailable
        providers do not fall back automatically.
      </p>
    </section>
  );
}
