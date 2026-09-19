import { act, renderHook } from "@testing-library/react";
import { useRealtime } from "./useRealtime";

beforeEach(() => {
  vi.stubGlobal("RTCPeerConnection", class {
    close = vi.fn();
  });
  vi.stubGlobal("Audio", class {
    pause = vi.fn();
  });
  vi.stubGlobal("navigator", { mediaDevices: {
    getUserMedia: () => new Promise(() => {}),
  } });
});
afterEach(() => vi.unstubAllGlobals());

function setup() {
  const send = vi.fn();
  const hook = renderHook(() => useRealtime(send));
  act(() => { void hook.result.current.start("browser", "token"); });
  const transcript = (turn_id: string, role: string, text: string, final = false) => {
    act(() => hook.result.current.control({ type: "realtime.transcript", turn_id, role, text, final }));
  };
  return { ...hook, send, transcript };
}

it("updates one turn for cumulative pieces and deduplicates final snapshots", () => {
  const { result, transcript } = setup();
  transcript("u1", "user", "Hel");
  transcript("u1", "user", "Hello world");
  transcript("u1", "user", "Hello world!", true);
  transcript("u1", "user", "Hello world!", true);
  transcript("u1", "user", "stale partial");
  transcript("a1", "assistant", "Hi");
  transcript("a1", "assistant", "Hi there.", true);
  expect(result.current.transcript).toBe(" You: Hello world! Agent: Hi there.");
});

it("keeps overlapping speakers separate and preserves interrupted text on stop", () => {
  const { result, send, transcript } = setup();
  transcript("a1", "assistant", "Let me");
  transcript("u1", "user", "Stop");
  transcript("a1", "assistant", "Let me check", true);
  transcript("u1", "user", "Stop that", true);
  transcript("u2", "user", "Another turn", true);
  transcript("a2", "assistant", "Partial reply");
  const expected = " Agent: Let me check You: Stop that You: Another turn Agent: Partial reply";
  expect(result.current.transcript).toBe(expected);
  act(() => result.current.stop());
  expect(send).toHaveBeenCalledWith({ type: "realtime.stop" });
  transcript("a2", "assistant", "late after stop", true);
  expect(result.current.transcript).toBe(expected);
  act(() => { void result.current.start("browser", "token"); });
  expect(result.current.transcript).toBe("");
  transcript("new-a1", "assistant", "Fresh", true);
  expect(result.current.transcript).toBe(" Agent: Fresh");
});

it("bounds display history and leaves standard transcript messages alone", () => {
  const { result, transcript } = setup();
  act(() => result.current.control({ type: "transcript", text: "standard mode" }));
  expect(result.current.transcript).toBe("");
  transcript("a1", "assistant", "x".repeat(7000));
  transcript("a1", "assistant", "x".repeat(7000), true);
  transcript("u1", "user", "Latest", true);
  expect(result.current.transcript.length).toBeLessThanOrEqual(6000);
  expect(result.current.transcript.endsWith(" You: Latest")).toBe(true);
});
