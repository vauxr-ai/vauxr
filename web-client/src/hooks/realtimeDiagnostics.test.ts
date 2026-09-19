import { observePlayback, realtimeDiagnostics } from "./realtimeDiagnostics";

beforeEach(() => { vi.useFakeTimers(); realtimeDiagnostics.disable(); });
afterEach(() => { vi.useRealTimers(); realtimeDiagnostics.disable(); });
function setup(getStats = vi.fn(async () => new Map())) {
  const pc = Object.assign(new EventTarget(), { getStats, connectionState: "connected", iceConnectionState: "connected" });
  const audio = document.createElement("audio");
  const observer = observePlayback(pc as unknown as RTCPeerConnection, audio);
  return { pc, audio, observer };
}
it("is opt-in and exports only selected numeric inbound audio stats", async () => {
  const stats = new Map([
    ["private-id", { type: "inbound-rtp", kind: "audio", packetsLost: 3, jitter: .02, audioLevel: .4,
      concealedSamples: 100, trackIdentifier: "secret", ssrc: 1234, token: "secret", packetsReceived: Infinity }],
    ["other", { type: "outbound-rtp", kind: "audio", bytesReceived: 999 }],
  ]);
  const getStats = vi.fn(async () => stats);
  const { observer, audio } = setup(getStats);
  await vi.advanceTimersByTimeAsync(1000);
  expect(getStats).not.toHaveBeenCalled();
  realtimeDiagnostics.enable();
  audio.dispatchEvent(new Event("waiting"));
  await vi.advanceTimersByTimeAsync(1000);
  const snapshot = realtimeDiagnostics.snapshot();
  expect(snapshot.entries.some(e => e.event === "playback.waiting")).toBe(true);
  expect(snapshot.entries.find(e => e.event === "inbound-rtp")).toMatchObject({ packetsLost: 3, jitter: .02, audioLevel: .4, concealedSamples: 100 });
  expect(JSON.stringify(snapshot)).not.toMatch(/secret|ssrc|trackIdentifier|packetsReceived|999/);
  snapshot.entries.length = 0;
  expect(realtimeDiagnostics.snapshot().entries.length).toBeGreaterThan(0);
  observer.stop();
});
it("bounds retention and removes listeners and polling on stop", async () => {
  realtimeDiagnostics.enable();
  const { observer, audio, pc } = setup();
  for (let i = 0; i < 400; i++) audio.dispatchEvent(new Event("waiting"));
  expect(realtimeDiagnostics.snapshot().entries).toHaveLength(300);
  observer.stop();
  const before = realtimeDiagnostics.snapshot();
  audio.dispatchEvent(new Event("playing"));
  await vi.advanceTimersByTimeAsync(3000);
  expect(pc.getStats).not.toHaveBeenCalled();
  expect(realtimeDiagnostics.snapshot()).toEqual(before);
  realtimeDiagnostics.disable();
  expect(realtimeDiagnostics.snapshot().entries).toEqual([]);
});
it("does not overlap polls or append an in-flight report after stop", async () => {
  let resolve!: (stats: Map<string, object>) => void;
  const getStats = vi.fn(() => new Promise<Map<string, object>>(r => { resolve = r; }));
  realtimeDiagnostics.enable();
  const { observer } = setup(getStats);
  await vi.advanceTimersByTimeAsync(5000);
  expect(getStats).toHaveBeenCalledTimes(1);
  observer.stop();
  const before = realtimeDiagnostics.snapshot();
  resolve(new Map([["a", { type: "inbound-rtp", kind: "audio", packetsLost: 1 }]]));
  await vi.advanceTimersByTimeAsync(1000);
  expect(realtimeDiagnostics.snapshot()).toEqual(before);
});

it("discards pending stats when the diagnostic buffer is cleared", async () => {
  let resolve!: (stats: Map<string, object>) => void;
  realtimeDiagnostics.enable();
  const { observer } = setup(vi.fn(() => new Promise<Map<string, object>>(r => { resolve = r; })));
  await vi.advanceTimersByTimeAsync(1000);
  realtimeDiagnostics.clear();
  resolve(new Map([["a", { type: "inbound-rtp", kind: "audio", packetsLost: 1 }]]));
  await Promise.resolve();
  expect(realtimeDiagnostics.snapshot().entries).toEqual([]);
  observer.stop();
});
