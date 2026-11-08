import AgentPipeline from "./AgentPipeline";
import RefreshButton from "./RefreshButton";
import { ownerFetch } from "../auth/api";
import { useCallback, useEffect, useId, useRef, useState } from "react";

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
  agentName, browserDeviceId, browserConnected = false, browserCredential,
}: {
  agentName?: string;
  browserDeviceId?: string;
  browserConnected?: boolean;
  browserCredential?: () => string | null;
  deviceId?: string;
  agentId?: string;
  onModeChange?: (mode: "standard" | "realtime" | null) => void;
}) {
  const tabsId = useId();
  const [previewStatus, setPreviewStatus] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const preview = useRef<{controller?: AbortController; audio?: HTMLAudioElement; url?: string}>({});
  const stopPreview = useCallback(() => {
    preview.current.controller?.abort();
    preview.current.audio?.pause();
    if (preview.current.url) URL.revokeObjectURL(preview.current.url);
    preview.current = {};
    setPreviewBusy(false);
  }, []);
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState("");
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
    setSaved("");
    try {
      setData(await request(patch));
      if (patch) setSaved("Voice settings saved. Applies to new turns.");
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
  const shownMode = agentId ? (data?.policy?.integration_modes.includes(editMode) ? editMode : data?.policy?.integration_modes[0] || "standard") : data?.voice?.mode || "standard";
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
  const selectedVoice = selectedTts ? defaults?.voices[selectedTts.id] || selectedTts.voices[0] || "" : "";
  const canPreview = !!(browserConnected && browserDeviceId && browserCredential?.() && selectedTts?.kind === "tts" && selectedTts.readiness === "ready" && selectedTts.voices.includes(selectedVoice));
  useEffect(() => {
    stopPreview();
    setPreviewStatus("");
    return stopPreview;
  }, [selectedVoice, selectedTts?.id, browserConnected, browserDeviceId, shownMode, stopPreview]);
  async function testVoice() {
    stopPreview();
    if (!canPreview || !selectedTts) return;
    const controller = new AbortController();
    preview.current = { controller };
    setPreviewBusy(true);
    setPreviewStatus("Preparing voice sample…");
    try {
      const response = await ownerFetch("/api/speech/preview", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Vauxr-Browser": browserCredential?.() || "" },
        body: JSON.stringify({device_id: browserDeviceId, tts_backend: selectedTts.id, voice: selectedVoice}),
        signal: controller.signal,
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw new Error(failure.error || `Voice preview failed (${response.status})`);
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      preview.current = { controller, audio, url };
      audio.onended = () => { if (!controller.signal.aborted) { stopPreview(); setPreviewStatus("Voice sample finished."); } };
      audio.onerror = () => { if (!controller.signal.aborted) { stopPreview(); setPreviewStatus("Voice sample playback failed."); } };
      await audio.play();
      if (controller.signal.aborted) { audio.pause(); return; }
      if (!controller.signal.aborted) setPreviewStatus("Playing in this browser.");
    } catch (err) {
      if (!controller.signal.aborted) {
        stopPreview();
        setPreviewStatus(err instanceof Error ? err.message : "Voice preview failed.");
      }
    }
  }
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
          ? "Voice pipeline"
          : deviceId
            ? "Device speech"
            : "Global speech defaults"}
      </h2>
      {!agentId && <p className="text-xs text-zinc-400">
        Global and device selections save immediately; agent provider edits require Save voice configuration. Defaults apply to new turns. Stop voice before changing its agent or
        mode; reconnect WebRTC for provider changes. Voices belong to a
        configured model.
      </p>}
      {!data && !error && <p role="status">Loading voice configuration…</p>}
      {!data && error && <RefreshButton label="Retry voice configuration" busy={busy} onClick={() => update()} />}
      {busy && <p role="status">Checking voice configuration…</p>}
      {saved && <p role="status">{saved}</p>}
      {error && <><p role="alert">{error}</p><p>Refresh to check the stored configuration before retrying a change.</p></>}
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
          <div style={{display: "flex", justifyContent: "flex-end", gridColumn: "1 / -1"}}>
            <RefreshButton label={agentId && hasProviderDraft ? "Discard provider edits and refresh" : "Refresh speech"} busy={busy} onClick={() => update()} />
          </div>
          {agentId ? <>
            <ModeTabs id={tabsId} modes={data.policy?.integration_modes || ["standard"]} value={shownMode} onChange={setEditMode} />
          </> : null}
          <div role={agentId ? "tabpanel" : undefined} id={`${tabsId}-panel`} aria-labelledby={agentId ? `${tabsId}-${shownMode}` : undefined} className={agentId ? "pipeline-editor" : "speech-provider-fields"}>
          {agentId ? <>
            <AgentPipeline onEdit={(kind) => {
              const labels: Record<string, string> = {stt: "STT backend", tts: "TTS backend", realtime: "Realtime provider / model"};
              const target = labels[kind];
              if (target) Array.from(document.querySelectorAll<HTMLSelectElement>(".agent-detail-modal select")).find(el => Array.from(el.labels || []).some(label => label.textContent === target))?.focus();
            }} mode={shownMode} agentName={agentName || data.policy?.agents.find(a => a.id === agentId)?.name || agentId} stt={defaults?.stt_backend} tts={defaults?.tts_backend} realtime={liveBackend} enabled={data.policy?.enabled_modes.includes(shownMode) || false} isDefault={data.policy?.default_mode === shownMode} />
          </> : (
          <SpeechSelect
            label="Voice mode"
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
          )}
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
              <div className={agentId ? "voice-test-row" : "speech-voice-field"}>
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
              {agentId && <div className="speech-preview">
                <button type="button" disabled={!canPreview || previewBusy} onClick={() => void testVoice()} aria-label="Test selected voice"><svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18"><path d="M11 4 5 9H2v6h3l6 5V4Zm4 4c3 2 3 6 0 8m3-11c5 4 5 10 0 14" fill="none" stroke="currentColor" strokeWidth="2"/></svg> Test</button>
                {previewBusy && <button type="button" onClick={() => { stopPreview(); setPreviewStatus("Voice sample stopped."); }}>Stop</button>}
                {!browserConnected && <p>Connect browser voice to test a sample.</p>}
                {previewStatus && <p role="status">{previewStatus}</p>}
              </div>}
              </div>
            </>
          )}
          {agentId && shownMode === "realtime" && <p>Voice samples are available for Standard text-to-speech providers only.</p>}
          </div>
          <div className="speech-actions">
            {deviceId && (
              <button
                className={style}
                onClick={() => void update({ reset: true })}
              >
                Reset to defaults
              </button>
            )}

          </div>
        </fieldset>
      )}
      {!agentId && shownMode === "realtime" && data && !chatOnly && (
        <p className="text-sm speech-selection">
          {agentId && hasProviderDraft ? "Draft Live:" : "Effective Live:"}{" "}
          {liveBackend || "unconfigured"} /{" "}
          {selectedLive?.model || "unconfigured"} /{" "}
          {liveVoice || "unconfigured"}
        </p>
      )}
      {!agentId && shownMode !== "realtime" && effective && !chatOnly && (
        <p className="text-sm">
          Effective: {effective.stt_backend} / {effective.tts_backend} /{" "}
          {effective.voice_id}
        </p>
      )}
      {!agentId && <p className="text-xs text-zinc-500">
        Readiness checks provider availability, not model inference. Unavailable
        providers do not fall back automatically.
      </p>}
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
  const initialEnabled = policy.enabled_modes.filter(mode => policy.integration_modes.includes(mode));
  const [enabled, setEnabled] = useState(initialEnabled);
  const [defaultMode, setDefaultMode] = useState(initialEnabled.includes(policy.default_mode || "") ? policy.default_mode : initialEnabled[0] || null);
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
        {["standard", "realtime"].map((mode) => (
          <label key={mode} className="speech-check">
            <input
              type="checkbox"
              role="switch"
              aria-label={mode === "realtime" ? "Enable Live" : "Enable Standard"}
              checked={policy.integration_modes.includes(mode) && enabled.includes(mode)}
              disabled={!policy.integration_modes.includes(mode)}
              onChange={(event) => {
                const next = event.target.checked ? [...enabled, mode] : enabled.filter(item => item !== mode);
                setEnabled(next);
                if (!next.length) setDefaultMode(null);
                else if (!defaultMode || !next.includes(defaultMode)) setDefaultMode(next[0]);
              }}
            />{" "}
            {mode === "realtime" ? "Enable Live" : "Enable Standard"}
            {!policy.integration_modes.includes(mode) && <span> (unavailable for this agent)</span>}
          </label>
        ))}
        <fieldset className="pipeline-defaults">
          <legend>Default voice mode</legend>
          {["standard", "realtime"].map(mode => <label className="speech-check" key={mode}>
            <input type="checkbox" role="switch" aria-label={mode === "standard" ? "Default Standard" : "Default Live"} checked={defaultMode === mode && enabled.includes(mode)} disabled={!enabled.includes(mode)} onChange={() => setDefaultMode(mode)} />
            {mode === "standard" ? "Standard default" : "Live default"}
          </label>)}
        </fieldset>
        <button type="submit" className="button-primary"
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


function ModeTabs({id, modes, value, onChange}: {id: string; modes: string[]; value: string; onChange: (value: string) => void}) {
  return <div role="tablist" aria-label="Configure mode providers" className="pipeline-tabs">
    {modes.map((mode, index) => <button key={mode} id={`${id}-${mode}`} type="button" role="tab" aria-controls={`${id}-panel`} aria-selected={value === mode} tabIndex={value === mode ? 0 : -1}
      onClick={() => onChange(mode)} onKeyDown={event => {
        const next = event.key === "Home" ? 0 : event.key === "End" ? modes.length - 1 : event.key === "ArrowRight" ? (index + 1) % modes.length : event.key === "ArrowLeft" ? (index - 1 + modes.length) % modes.length : -1;
        if (next >= 0) { event.preventDefault(); onChange(modes[next]); document.getElementById(`${id}-${modes[next]}`)?.focus(); }
      }}>{mode === "realtime" ? "Live / Realtime" : "Standard"}</button>)}
  </div>;
}
