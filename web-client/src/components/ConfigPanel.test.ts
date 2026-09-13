import { validateVoiceUrl } from "./ConfigPanel";
afterEach(() => vi.unstubAllGlobals());
it("uses exact owner authority and rejects TLS downgrade, other hosts, secrets and query strings", () => {
  vi.stubGlobal("location", {
    protocol: "https:",
    host: "voice.example",
    hostname: "voice.example",
  });
  expect(validateVoiceUrl("wss://voice.example/ws")).toBe(
    "wss://voice.example/ws",
  );
  for (const url of [
    "ws://voice.example/ws",
    "wss://other.example/ws",
    "wss://voice.example:8765/ws",
    "wss://user:secret@voice.example/ws",
    "wss://voice.example/ws?token=secret",
    "wss://voice.example/ws#secret",
  ])
    expect(() => validateVoiceUrl(url)).toThrow();
});
