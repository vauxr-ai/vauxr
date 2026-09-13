import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SpeechSettings from "./SpeechSettings";

const view = {
  defaults: { stt_backend: "whisper", tts_backend: "piper", voices: { piper: "amy", kokoro: "af" } },
  overrides: {}, effective: { stt_backend: "whisper", tts_backend: "piper", voice_id: "amy" }, error: null,
  backends: [
    { id: "whisper", kind: "stt", model: "small", voices: [], readiness: "ready" },
    { id: "piper", kind: "tts", model: "piper-model", voices: ["amy", "sam"], readiness: "ready" },
    { id: "kokoro", kind: "tts", model: "kokoro-model", voices: ["af"], readiness: "unavailable" },
  ],
};
afterEach(() => vi.unstubAllGlobals());

it("shows effective inheritance, availability, and model-scoped choices; resets all overrides", async () => {
  const fetcher = vi.fn().mockImplementation(async () => ({ ok: true, json: async () => view }));
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings baseUrl="http://localhost" token="test" deviceId="a/b" />);
  expect(await screen.findByText("Effective: whisper / piper / amy")).toBeTruthy();
  expect(screen.getByText("kokoro · kokoro-model · unavailable")).toBeTruthy();
  expect(screen.queryByRole("option", { name: "af" })).toBeNull();
  expect((screen.getByLabelText("TTS backend") as HTMLSelectElement).value).toBe("");
  fireEvent.change(screen.getByLabelText("Voice"), { target: { value: "sam" } });
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ voices: { piper: "sam" } });
  await waitFor(() => expect((screen.getByText("Reset to defaults") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Reset to defaults"));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(fetcher.mock.calls[2][0]).toBe("http://localhost/api/devices/a%2Fb/speech");
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ stt_backend: null, tts_backend: null, voices: null });
  expect(fetcher.mock.calls[2][1].headers.Authorization).toBe("Bearer test");
});

it("refreshes inheritors and scopes voice controls after a global model change", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => view })
    .mockResolvedValue({ ok: true, json: async () => ({ ...view,
      defaults: { ...view.defaults, tts_backend: "kokoro" },
      effective: { ...view.effective, tts_backend: "kokoro", voice_id: "af" },
    }) });
  vi.stubGlobal("fetch", fetcher);
  render(<SpeechSettings baseUrl="http://localhost" token="test" deviceId="b" />);
  await screen.findByText("Effective: whisper / piper / amy");
  fireEvent.click(screen.getByText("Refresh speech"));
  await screen.findByText("Effective: whisper / kokoro / af");
  expect(screen.getByRole("option", { name: "af" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "amy" })).toBeNull();
});

it("reports authorization failure without offering edits", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
  render(<SpeechSettings baseUrl="http://localhost" token="bad" />);
  expect((await screen.findByRole("alert")).textContent).toContain("401");
  expect(screen.queryByLabelText("TTS backend")).toBeNull();
});

it("handles an incompatible server response without crashing the panel", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
  render(<SpeechSettings baseUrl="http://localhost" token="test" />);
  expect((await screen.findByRole("alert")).textContent).toBe("Invalid speech settings response");
});
