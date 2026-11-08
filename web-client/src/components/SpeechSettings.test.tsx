import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { setCsrf } from "../auth/api";
import SpeechSettings from "./SpeechSettings";

const view = {
  defaults: {
    stt_backend: "whisper",
    tts_backend: "piper",
    voices: { piper: "amy", kokoro: "af" },
  },
  overrides: {},
  effective: { stt_backend: "whisper", tts_backend: "piper", voice_id: "amy" },
  error: null,
  backends: [
    {
      id: "whisper",
      kind: "stt",
      model: "small",
      voices: [],
      readiness: "ready",
    },
    {
      id: "piper",
      kind: "tts",
      model: "piper-model",
      voices: ["amy", "sam"],
      readiness: "ready",
    },
    {
      id: "kokoro",
      kind: "tts",
      model: "kokoro-model",
      voices: ["af"],
      readiness: "unavailable",
    },
  ],
};
afterEach(() => {
  vi.unstubAllGlobals();
  setCsrf("");
});

it("shows effective inheritance, availability, and model-scoped choices; resets all overrides", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(async () => ({ ok: true, json: async () => view }));
  vi.stubGlobal("fetch", fetcher);
  setCsrf("csrf-test");
  render(<SpeechSettings deviceId="a/b" />);
  expect(
    await screen.findByText("Effective: whisper / piper / amy"),
  ).toBeTruthy();
  expect(screen.getByText("kokoro · kokoro-model · unavailable")).toBeTruthy();
  expect(screen.queryByRole("option", { name: "af" })).toBeNull();
  expect(
    (screen.getByLabelText("TTS backend") as HTMLSelectElement).value,
  ).toBe("");
  fireEvent.change(screen.getByLabelText("Voice"), {
    target: { value: "sam" },
  });
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    voices: { piper: "sam" },
  });
  await waitFor(() =>
    expect(
      (screen.getByText("Reset to defaults") as HTMLButtonElement).disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByText("Reset to defaults"));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(fetcher.mock.calls[2][0]).toBe("/api/devices/a%2Fb/speech");
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({
    reset: true,
  });
  for (const [path, init] of fetcher.mock.calls) {
    expect(path).toBe("/api/devices/a%2Fb/speech");
    expect(init).toMatchObject({
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
    });
    expect(init.headers.has("Authorization")).toBe(false);
    expect(init.headers.get("X-CSRF-Token")).toBe("csrf-test");
  }
});

it("refreshes inheritors and scopes voice controls after a global model change", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, json: async () => view })
    .mockResolvedValue({
      ok: true,
      json: async () => ({
        ...view,
        defaults: { ...view.defaults, tts_backend: "kokoro" },
        effective: { ...view.effective, tts_backend: "kokoro", voice_id: "af" },
      }),
    });
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings deviceId="b" />);
  await screen.findByText("Effective: whisper / piper / amy");
  fireEvent.click(screen.getByRole("button", {name: "Refresh speech"}));
  await screen.findByText("Effective: whisper / kokoro / af");
  expect(screen.getByRole("option", { name: "af" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "amy" })).toBeNull();
});

it("reports authorization failure without offering edits", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }),
  );
  render(<SpeechSettings />);
  expect((await screen.findByRole("alert")).textContent).toContain("401");
  expect(screen.queryByLabelText("TTS backend")).toBeNull();
});

it("handles an incompatible server response without crashing the panel", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => [] }),
  );
  render(<SpeechSettings />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Invalid speech settings response",
  );
});

it.each([undefined, "device-a"])(
  "publishes scoped settings changes and refreshes for %s",
  async (deviceId) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => view }),
    );
    const changed = vi.fn();
    window.addEventListener("speech-settings-changed", changed);
    try {
      render(<SpeechSettings deviceId={deviceId} />);
      await screen.findByText("Effective: whisper / piper / amy");
      fireEvent.change(screen.getByLabelText("Voice"), {
        target: { value: "sam" },
      });
      await waitFor(() => expect(changed).toHaveBeenCalledTimes(1));
      expect(changed.mock.calls[0][0].detail).toEqual({
        source: "settings",
        scope: deviceId ? "device" : "global",
        deviceId,
      });
      await waitFor(() =>
        expect(screen.getByRole("button", {name: "Refresh speech"})).toBeEnabled(),
      );
      fireEvent.click(screen.getByRole("button", {name: "Refresh speech"}));
      await waitFor(() => expect(changed).toHaveBeenCalledTimes(2));
      expect(changed.mock.calls[1][0].detail).toEqual(
        changed.mock.calls[0][0].detail,
      );
    } finally {
      window.removeEventListener("speech-settings-changed", changed);
    }
  },
);

it.each([undefined, "phone-device"])(
  "keeps full long selections readable and model-scoped for %s",
  async (deviceId) => {
    const backend = "piper_household_provider_with_a_long_identifier";
    const model =
      "populated_model_name_that_does_not_fit_a_native_phone_select";
    const voice = "en_US_household_voice_with_a_long_model_scoped_identifier";
    const longView = {
      ...view,
      defaults: {
        ...view.defaults,
        tts_backend: backend,
        voices: { [backend]: voice },
      },
      effective: { ...view.effective, tts_backend: backend, voice_id: voice },
      backends: [
        ...view.backends,
        {
          id: backend,
          kind: "tts",
          model,
          voices: [voice],
          readiness: "unavailable",
        },
      ],
    };
    const fetcher = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => longView });
    vi.stubGlobal("fetch", fetcher);
    render(<SpeechSettings deviceId={deviceId} />);
    const tts = await screen.findByRole("combobox", {
      name: "TTS backend",
      exact: true,
    });
    expect(tts).toHaveAccessibleDescription(
      deviceId
        ? `Selected: Inherit agent (${backend})`
        : `Selected: ${backend} · ${model} · unavailable`,
    );
    expect(
      screen.getByRole("combobox", { name: "Voice", exact: true }),
    ).toHaveAccessibleDescription(
      deviceId
        ? `Selected: Inherit model default (${voice})`
        : `Selected: ${voice}`,
    );
    fireEvent.change(
      screen.getByRole("combobox", { name: "Voice", exact: true }),
      { target: { value: voice } },
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      voices: { [backend]: voice },
    });
  },
);

it("saves Live-only enablement and default atomically, retaining the draft on an actionable conflict", async () => {
  const policy = {
    agent_id: "agent",
    assigned: false,
    configured: true,
    integration_modes: ["standard", "realtime"],
    enabled_modes: ["standard", "realtime"],
    default_mode: "standard",
    available_modes: ["standard", "realtime"],
    capabilities_known: false,
    conflict: null,
    agents: [{ id: "agent", name: "Household" }],
  };
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => ({ ...view, policy }) });
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings agentId="agent" />);
  const standard = await screen.findByLabelText("Enable Standard");
  fireEvent.click(standard);
  fireEvent.click(screen.getByRole("switch", {name: "Default Live"}));
  fetcher.mockResolvedValueOnce({
    ok: false,
    status: 409,
    json: async () => ({
      error: "kitchen-speaker: reset its Standard override",
    }),
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save voice configuration" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "kitchen-speaker: reset its Standard override",
  );
  expect(standard).not.toBeChecked();
  expect(screen.getByRole("switch", {name: "Default Live"})).toBeChecked();
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    enabled_modes: ["realtime"],
    default_mode: "realtime",
  });
  expect(fetcher.mock.calls[1][0]).toBe("/api/agents/agent/speech");
  fireEvent.click(screen.getByLabelText("Enable Live"));
  expect(
    screen.getByRole("button", { name: "Save voice configuration" }),
  ).toBeEnabled();
  expect(screen.getByText(/Chat-only: text conversation remains available/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Save voice configuration" }));
  await waitFor(() => expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({
    enabled_modes: [], default_mode: null,
  }));
});

it("offers explicit assignment with reset and never discards overrides after a rejected assignment", async () => {
  const policy = {
    agent_id: "old",
    assigned: true,
    configured: true,
    integration_modes: ["standard", "realtime"],
    enabled_modes: ["standard"],
    default_mode: "standard",
    available_modes: ["standard"],
    capabilities_known: true,
    conflict: null,
    agents: [
      { id: "old", name: "Household" },
      { id: "new", name: "Workshop" },
    ],
  };
  const fetcher = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ ...view, policy, overrides: { mode: "standard" } }),
  });
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings deviceId="speaker" />);
  const assignment = await screen.findByLabelText("Assigned agent");
  fireEvent.change(assignment, { target: { value: "new" } });
  fetcher.mockResolvedValueOnce({
    ok: false,
    status: 409,
    json: async () => ({
      error: "speaker: incompatible override. Reset or change it.",
    }),
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save agent assignment" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "incompatible override",
  );
  expect(assignment).toHaveValue("new");
  expect(screen.getByLabelText("Voice mode")).toHaveValue("standard");
  fireEvent.click(
    screen.getByLabelText("Reset voice overrides when assigning"),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Save agent assignment" }),
  );
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({
    agent_id: "new",
    reset: true,
  });
});

it("stages a disabled Live provider and saves provider, voice, enablement and default in one request", async () => {
  const policy = {
    agent_id: "agent",
    assigned: false,
    configured: true,
    integration_modes: ["standard", "realtime"],
    enabled_modes: ["standard"],
    default_mode: "standard",
    available_modes: ["standard"],
    capabilities_known: true,
    conflict: null,
    agents: [{ id: "agent", name: "Household" }],
  };
  const live = {
    id: "live-provider",
    kind: "realtime",
    model: "live-model",
    voices: ["sage", "ash"],
    readiness: "unavailable",
  };
  const fetcher = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      ...view,
      policy,
      backends: [...view.backends, live],
    }),
  });
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings agentId="agent" />);
  await screen.findByLabelText("Enable Standard");
  fireEvent.click(screen.getByRole("tab", {name: "Live / Realtime"}));
  fireEvent.change(screen.getByLabelText("Realtime provider / model"), {
    target: { value: "live-provider" },
  });
  fireEvent.change(screen.getByLabelText("Realtime voice"), {
    target: { value: "ash" },
  });
  expect(fetcher).toHaveBeenCalledTimes(1); // Provider preparation is not an enablement request.
  expect(
    screen.getByText("Draft Live: live-provider / live-model / ash"),
  ).toBeVisible();
  fireEvent.click(screen.getByLabelText("Enable Standard"));
  fireEvent.click(screen.getByLabelText("Enable Live"));
  fireEvent.click(screen.getByRole("switch", {name: "Default Live"}));
  fetcher.mockResolvedValueOnce({
    ok: false,
    status: 409,
    json: async () => ({ error: "D1: reconnect with WebRTC first" }),
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save voice configuration" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "D1: reconnect with WebRTC first",
  );
  expect(screen.getByLabelText("Realtime provider / model")).toHaveValue(
    "live-provider",
  );
  expect(screen.getByLabelText("Realtime voice")).toHaveValue("ash");
  expect(screen.getByLabelText("Enable Standard")).not.toBeChecked();
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
    realtime_backend: "live-provider",
    realtime_voice: "ash",
    enabled_modes: ["realtime"],
    default_mode: "realtime",
  });
  fetcher.mockResolvedValueOnce({
    ok: true,
    json: async () => ({
      ...view,
      defaults: {
        ...view.defaults,
        mode: "realtime",
        realtime_backend: "live-provider",
        realtime_voice: "ash",
      },
      voice: {
        mode: "realtime",
        realtime_backend: "live-provider",
        realtime_model: "live-model",
        realtime_voice: "ash",
      },
      policy: {
        ...policy,
        enabled_modes: ["realtime"],
        default_mode: "realtime",
      },
      backends: [...view.backends, live],
    }),
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Save voice configuration" }),
  );
  await waitFor(() =>
    expect(screen.queryByText(/Unsaved provider changes/)).toBeNull(),
  );
  expect(
    screen.getByText("Effective Live: live-provider / live-model / ash"),
  ).toBeVisible();
});

const previewPolicy = {
  agent_id: "agent", assigned: false, configured: true,
  integration_modes: ["standard", "realtime"], enabled_modes: ["standard"],
  default_mode: "standard", available_modes: ["standard"],
  capabilities_known: true, conflict: null, agents: [{id: "agent", name: "Household"}],
};
it("navigates provider tabs by keyboard and disables disconnected previews", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true, json: async () => ({...view, policy: previewPolicy})}));
  render(<SpeechSettings agentId="agent" agentName="Workshop" />);
  expect(await screen.findByRole("button", {name: "Test selected voice"})).toBeDisabled();
  const standard = screen.getByRole("tab", {name: "Standard"});
  fireEvent.keyDown(standard, {key: "End"});
  expect(screen.getByRole("tab", {name: "Live / Realtime"})).toHaveFocus();
  expect(screen.getByText(/samples are available for Standard/)).toBeVisible();
  fireEvent.keyDown(screen.getByRole("tab", {name: "Live / Realtime"}), {key: "ArrowRight"});
  expect(standard).toHaveFocus();
});
it("cancels preview on draft selection change without persisting selection", async () => {
  let signal: AbortSignal | undefined;
  const fetcher = vi.fn().mockImplementation(async (path, init) => {
    if (path === "/api/speech/preview") { signal = init.signal; return new Promise(() => {}); }
    return {ok: true, json: async () => ({...view, policy: previewPolicy})};
  });
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings agentId="agent" browserDeviceId="browser" browserConnected browserCredential={() => "browser-proof"} />);
  fireEvent.click(await screen.findByRole("button", {name: "Test selected voice"}));
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({device_id: "browser", tts_backend: "piper", voice: "amy"});
  expect(new Headers(fetcher.mock.calls[1][1].headers).get("X-Vauxr-Browser")).toBe("browser-proof");
  fireEvent.change(screen.getByLabelText("Voice"), {target: {value: "sam"}});
  expect(signal?.aborted).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", {name: "Stop"})).toBeNull();
});
it("reports preview errors and disables unavailable providers", async () => {
  const fetcher = vi.fn().mockResolvedValue({ok: true, json: async () => ({...view, policy: previewPolicy})});
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings agentId="agent" browserDeviceId="browser" browserConnected browserCredential={() => "browser-proof"} />);
  const test = await screen.findByRole("button", {name: "Test selected voice"});
  fetcher.mockResolvedValueOnce({ok: false, status: 503, json: async () => ({error: "Synthesis unavailable"})});
  fireEvent.click(test);
  expect(await screen.findByText("Synthesis unavailable")).toBeVisible();
  fireEvent.change(screen.getByLabelText("TTS backend"), {target: {value: "kokoro"}});
  expect(test).toBeDisabled();
});
it.each([false, true])("cleans up local playback on disconnect and reports playback rejection (%s)", async (reject) => {
  const pause = vi.fn();
  const play = reject ? vi.fn().mockRejectedValue(new Error("Playback denied")) : vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("Audio", class {pause = pause; play = play;});
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const revoke = vi.fn();
  URL.createObjectURL = vi.fn().mockReturnValue("blob:sample");
  URL.revokeObjectURL = revoke;
  try {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async path => path === "/api/speech/preview"
      ? {ok: true, blob: async () => new Blob(["wav"], {type: "audio/wav"})}
      : {ok: true, json: async () => ({...view, policy: previewPolicy})}));
    const {rerender} = render(<SpeechSettings agentId="agent" browserDeviceId="browser" browserConnected browserCredential={() => "browser-proof"} />);
    fireEvent.click(await screen.findByRole("button", {name: "Test selected voice"}));
    await screen.findByText(reject ? "Playback denied" : "Playing in this browser.");
    expect(play).toHaveBeenCalledTimes(1);
    rerender(<SpeechSettings agentId="agent" browserDeviceId="browser" browserConnected={false} browserCredential={() => "browser-proof"} />);
    expect(pause).toHaveBeenCalled();
    expect(revoke).toHaveBeenCalledWith("blob:sample");
  } finally {
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

it("recovers initial load failure through the accessible refresh action", async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error("Offline")).mockResolvedValue({ok:true,json:async()=>view});
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings />);
  fireEvent.click(await screen.findByRole("button", {name:"Retry voice configuration"}));
  await screen.findByLabelText("TTS backend");
  expect(fetcher).toHaveBeenCalledTimes(2);
});
