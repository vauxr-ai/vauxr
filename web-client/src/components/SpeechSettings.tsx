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
  mode?: "standard" | "realtime" | null;
  realtime_backend?: string;
  realtime_voice?: string;
  stt_backend: string;
  tts_backend: string;
  voices: Record<string, string>;
};
type Policy = {
  fallback_agent?: string;
  agent_id: string;
  assigned: boolean;
  configured: boolean;
  integration_modes: string[];
  enabled_modes: string[];
  default_mode: string | null;
  chat_only?: boolean;
  available_modes: string[];
  capabilities_known: boolean;
  conflict: string | null;
  agents: { id: string; name: string }[];
};
type View = {
  policy?: Policy;
  voice?: {
    mode: "standard" | "realtime" | null;
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
  agentId,
  onModeChange,
}: {
  deviceId?: string;
  agentId?: string;
  onModeChange?: (mode: "standard" | "realtime" | null) => void;
}) {
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [providerDraft, setProviderDraft] = useState<Partial<Defaults>>({});
  const [editMode, setEditMode] = useState("standard");
  const [agentDraft, setAgentDraft] = useState("");
  const [resetAssignment, setResetAssignment] = useState(false);
  const path = agentId
    ? `/api/agents/${encodeURIComponent(agentId)}/speech`
    : deviceId
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
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(
          failure.error ||
            `Speech settings request failed (${response.status})`,
        );
      }
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
    setProviderDraft({});
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
    if (agentId && patch && !("enabled_modes" in patch)) {
      const selection = patch as Partial<Defaults>;
      setProviderDraft((current) => ({
        ...current,
        ...selection,
        ...(selection.voices
          ? { voices: { ...current.voices, ...selection.voices } }
          : {}),
      }));
      return;
    }
    setBusy(true);
    setError("");
    try {
      setData(await request(patch));
      setProviderDraft({});
      window.dispatchEvent(
        new CustomEvent("speech-settings-changed", {
          detail: {
            source: "settings",
            scope: deviceId ? "device" : "global",
            deviceId,
            agentId,
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
    if (data) onModeChange?.(data.policy?.chat_only ? null : data.voice?.mode || "standard");
  }, [data, onModeChange]);
  useEffect(() => {
    setAgentDraft(data?.policy?.assigned ? data.policy.agent_id : "");
  }, [data]);
  const defaults =
    agentId && data
      ? {
          ...data.defaults,
          ...providerDraft,
          voices: { ...data.defaults.voices, ...providerDraft.voices },
        }
      : data?.defaults;
  const chatOnly = data?.policy?.chat_only === true;
  const hasProviderDraft = Object.keys(providerDraft).length > 0;
  const effective = data?.effective;
  const shownMode = agentId ? editMode : data?.voice?.mode || "standard";
  const liveBackend = agentId
    ? defaults?.realtime_backend
    : data?.voice?.realtime_backend;
  const liveVoice = agentId
    ? defaults?.realtime_voice
    : data?.voice?.realtime_voice;
  const selectedLive = data?.backends.find(
    (backend) => backend.id === liveBackend,
  );
  const liveVoices = selectedLive?.voices || [];
  const modes =
    (deviceId && data?.policy?.assigned) ||
    (deviceId && data?.policy?.configured)
      ? data?.policy?.available_modes || []
      : ["standard", "realtime"];
  const selectedTts = data?.backends.find(
    (b) => b.id === (agentId ? defaults?.tts_backend : effective?.tts_backend),
  );
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
      aria-label={
        agentId ? "Agent voice" : deviceId ? "Device speech" : "Global speech"
      }
      className="speech-settings card space-y-3 p-4"
    >
      <h2 className="font-semibold">
        {agentId
          ? "Agent voice defaults"
          : deviceId
            ? "Device speech"
            : "Global speech defaults"}
      </h2>
      <p className="text-xs text-zinc-400">
        Defaults apply to new turns. Stop voice before changing its agent or
        mode; reconnect WebRTC for provider changes. Voices belong to a
        configured model.
      </p>
      {error && <p role="alert">{error}</p>}
      {data?.error && <p role="alert">{data.error}</p>}
      {data?.policy?.conflict && <p role="alert">{data.policy.conflict}</p>}
      {chatOnly && <p role="status">Chat-only · No voice modes enabled. Browser text is available; physical voice devices require a voice-enabled agent.</p>}
      {deviceId && data?.policy && (
        <fieldset disabled={busy} className="speech-fields">
          <SpeechSelect
            label="Assigned agent"
            value={agentDraft}
            options={[
              {
                value: "",
                label: `Follow active route (${data.policy.fallback_agent || "none"})`,
              },
              ...(data.policy.assigned &&
              !data.policy.agents.some((a) => a.id === data.policy?.agent_id)
                ? [
                    {
                      value: data.policy.agent_id,
                      label: `Unavailable agent (${data.policy.agent_id})`,
                    },
                  ]
                : []),
              ...data.policy.agents.map((a) => ({
                value: a.id,
                label: a.name,
              })),
            ]}
            onChange={setAgentDraft}
          />
          <label className="speech-check">
            <input
              type="checkbox"
              checked={resetAssignment}
              onChange={(event) => setResetAssignment(event.target.checked)}
            />{" "}
            Reset voice overrides when assigning
          </label>
          <button
            onClick={() =>
              void update({
                agent_id: agentDraft || null,
                ...(resetAssignment ? { reset: true } : {}),
              })
            }
          >
            Save agent assignment
          </button>
          <p>
            {data.policy.assigned ? "Assigned agent" : "Following active route"}
            .{" "}
            {Object.keys(data.overrides).length
              ? "Explicit voice overrides"
              : "Inherited voice defaults"}
            .
          </p>
          <p>
            Available:{" "}
            {data.policy.available_modes
              .map((mode) => (mode === "realtime" ? "Live" : "Standard"))
              .join(", ") || "none"}
            .{" "}
            {!data.policy.capabilities_known &&
              "Connect the device to verify WebRTC capability."}
          </p>
        </fieldset>
      )}
      {agentId && hasProviderDraft && (
        <p role="status">
          Unsaved provider changes. Save voice configuration applies modes and
          providers together.
        </p>
      )}
      {agentId && data?.policy && (
        <AgentModePolicy
          key={`${agentId}-${JSON.stringify(data.policy)}`}
          policy={data.policy}
          busy={busy}
          save={(policy) => update({ ...providerDraft, ...policy })}
        />
      )}
      {chatOnly && agentId && <p>Stored provider settings below are inactive while this agent is chat-only.</p>}
      {data && (!chatOnly || agentId) && (
        <fieldset disabled={busy} className="speech-fields">
          <SpeechSelect
            label={agentId ? "Configure mode providers" : "Voice mode"}
            value={
              agentId
                ? editMode
                : deviceId
                  ? data.overrides.mode || ""
                  : data.voice?.mode || "standard"
            }
            describeSelection={false}
            options={[
              ...(deviceId
                ? [
                    {
                      value: "",
                      label: `Inherit agent default (${chatOnly ? "Chat-only; no voice" : data.defaults.mode === "realtime" ? "Live" : "Standard"})`,
                    },
                  ]
                : []),
              ...(agentId
                ? data.policy?.integration_modes || ["standard"]
                : [
                    ...new Set([
                      ...modes,
                      ...(data.overrides.mode ? [data.overrides.mode] : []),
                    ]),
                  ]
              ).map((mode) => ({
                value: mode,
                label:
                  (mode === "realtime" ? "Live / Realtime" : "Standard") +
                  (agentId && !data.policy?.enabled_modes.includes(mode)
                    ? " (disabled)"
                    : ""),
              })),
            ]}
            onChange={(mode) =>
              agentId ? setEditMode(mode) : void update({ mode: mode || null })
            }
          />
          {shownMode === "realtime" ? (
            <>
              <SpeechSelect
                label="Realtime provider / model"
                value={
                  deviceId
                    ? data.overrides.realtime_backend || ""
                    : liveBackend || ""
                }
                options={[
                  ...(deviceId
                    ? [
                        {
                          value: "",
                          label: `Inherit agent (${data.defaults.realtime_backend || "unconfigured"})`,
                        },
                      ]
                    : []),
                  ...backendOptions("realtime"),
                ]}
                onChange={(realtime_backend) =>
                  void update({
                    realtime_backend: realtime_backend || null,
                    realtime_voice: realtime_backend
                      ? data.backends.find((b) => b.id === realtime_backend)
                          ?.voices[0]
                      : null,
                  })
                }
              />
              <SpeechSelect
                label="Realtime voice"
                value={
                  deviceId
                    ? data.overrides.realtime_voice || ""
                    : liveVoice || ""
                }
                options={[
                  ...(deviceId
                    ? [
                        {
                          value: "",
                          label: `Inherit agent (${data.defaults.realtime_voice || "unconfigured"})`,
                        },
                      ]
                    : []),
                  ...liveVoices.map((voice) => ({
                    value: voice,
                    label: voice,
                  })),
                ]}
                onChange={(realtime_voice) =>
                  void update({ realtime_voice: realtime_voice || null })
                }
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
                    label: `Inherit agent (${data.defaults[key]})`,
                  });
                return (
                  <SpeechSelect
                    key={kind}
                    label={`${kind.toUpperCase()} backend`}
                    value={
                      deviceId
                        ? data.overrides[key] || ""
                        : defaults?.[key] || ""
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
                      : defaults?.voices[selectedTts.id] ||
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
                onClick={() => void update({ reset: true })}
              >
                Reset to defaults
              </button>
            )}
            <button className={style} onClick={() => void update()}>
              {agentId && hasProviderDraft
                ? "Discard provider edits and refresh"
                : "Refresh speech"}
            </button>
          </div>
        </fieldset>
      )}
      {shownMode === "realtime" && data && !chatOnly && (
        <p className="text-sm speech-selection">
          {agentId && hasProviderDraft ? "Draft Live:" : "Effective Live:"}{" "}
          {liveBackend || "unconfigured"} /{" "}
          {selectedLive?.model || "unconfigured"} /{" "}
          {liveVoice || "unconfigured"}
        </p>
      )}
      {shownMode !== "realtime" && effective && !chatOnly && (
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

function AgentModePolicy({
  policy,
  busy,
  save,
}: {
  policy: Policy;
  busy: boolean;
  save: (patch: object) => Promise<void>;
}) {
  const [enabled, setEnabled] = useState(policy.enabled_modes);
  const [defaultMode, setDefaultMode] = useState(policy.default_mode);
  const changed =
    JSON.stringify(enabled) !== JSON.stringify(policy.enabled_modes) ||
    defaultMode !== policy.default_mode;
  return (
    <form
      className="speech-mode-policy"
      onSubmit={(event) => {
        event.preventDefault();
        void save({ enabled_modes: enabled, default_mode: enabled.length ? defaultMode : null });
      }}
    >
      <fieldset disabled={busy} className="speech-fields">
        <legend>Enabled voice modes</legend>
        {policy.integration_modes.map((mode) => (
          <label key={mode} className="speech-check">
            <input
              type="checkbox"
              checked={enabled.includes(mode)}
              onChange={(event) => {
                const next = event.target.checked ? [...enabled, mode] : enabled.filter(item => item !== mode);
                setEnabled(next);
                if (!next.length) setDefaultMode(null);
                else if (!defaultMode || !next.includes(defaultMode)) setDefaultMode(next[0]);
              }}
            />{" "}
            {mode === "realtime" ? "Enable Live" : "Enable Standard"}
          </label>
        ))}
        <SpeechSelect
          label="Default voice mode"
          value={defaultMode || ""}
          options={[
            ...(!enabled.length ? [{ value: "", label: "Chat-only (no voice default)" }] : []),
            ...(defaultMode && !policy.integration_modes.includes(defaultMode)
              ? [
                  {
                    value: defaultMode,
                    label: `Unavailable default (${defaultMode})`,
                  },
                ]
              : []),
            ...enabled.map((mode) => ({
              value: mode,
              label: mode === "realtime" ? "Live" : "Standard",
            })),
          ]}
          onChange={setDefaultMode}
        />
        <button
          disabled={busy || (enabled.length > 0 && !enabled.includes(defaultMode || ""))}
        >
          Save voice configuration
        </button>
        {changed && <p>Unsaved voice policy</p>}
        {!enabled.length && (
          <p role="status">Chat-only: text conversation remains available. No microphone, STT or TTS.</p>
        )}
      </fieldset>
    </form>
  );
}
