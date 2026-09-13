import { renderHook } from "@testing-library/react";
import { useAudio } from "./useAudio";

it.each(["permission", "worklet", "rejection"])("late %s completion only cleans up its own resources", async (stage) => {
  let release!: () => void;
  let reject!: (error: Error) => void;
  const pending = new Promise<void>((yes, no) => { release = yes; reject = no; });
  const tracks = [vi.fn(), vi.fn()];
  const streams = tracks.map(stop => ({ getTracks: () => [{ stop }] }));
  const contexts: any[] = [];
  vi.stubGlobal("AudioContext", class {
    close = vi.fn(async () => {});
    audioWorklet = { addModule: vi.fn(() => contexts.length === 1 && stage === "worklet" ? pending : Promise.resolve()) };
    createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
    constructor() { contexts.push(this); }
  });
  const nodes: any[] = [];
  vi.stubGlobal("AudioWorkletNode", class {
    port = { onmessage: null };
    disconnect = vi.fn();
    constructor() { nodes.push(this); }
  });
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
    getUserMedia: vi.fn().mockImplementationOnce(async () => {
      if (stage !== "worklet") await pending;
      return streams[0];
    }).mockResolvedValueOnce(streams[1]),
  } });
  const { result } = renderHook(() => useAudio({}));
  const first = result.current.startCapture();
  const failed = expect(first).rejects.toThrow();
  await Promise.resolve();
  result.current.stopCapture();
  await result.current.startCapture();
  if (stage === "rejection") reject(new Error("denied")); else release();
  await failed;
  expect(contexts[0].close).toHaveBeenCalledTimes(1);
  expect(tracks[0]).toHaveBeenCalledTimes(stage === "rejection" ? 0 : 1);
  expect(contexts[1].close).not.toHaveBeenCalled();
  expect(tracks[1]).not.toHaveBeenCalled();
  expect(nodes).toHaveLength(1);
  result.current.stopCapture();
  expect(tracks[1]).toHaveBeenCalledTimes(1);
  expect(nodes[0].disconnect).toHaveBeenCalledTimes(1);
});
