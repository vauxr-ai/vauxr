import { act, fireEvent, render, screen } from "@testing-library/react";
import App from "./App";

const mocks = vi.hoisted(() => ({
  audio: { startCapture: vi.fn(), stopCapture: vi.fn(), stopPlayback: vi.fn() },
  ws: { state: "connected", disconnect: vi.fn(), sendVoiceStart: vi.fn(), setState: vi.fn(), addLog: vi.fn(), log: [] },
}));
vi.mock("./auth/OwnerGate", () => ({ default: ({ children }: any) => children }));
vi.mock("./hooks/useAudio", () => ({ useAudio: () => mocks.audio }));
vi.mock("./hooks/useWebSocket", () => ({ useWebSocket: () => mocks.ws }));
vi.mock("./components/Layout", () => ({ default: ({ talk }: any) => talk }));
vi.mock("./components/TalkPanel", () => ({ default: (props: any) => <button onClick={props.onTalkStart}>{props.talking ? "Talking" : "Start"}</button> }));

it.each(["reject", "resolve"])("stale capture %s cannot stop a newer capture after reconnect", async (outcome) => {
  vi.stubGlobal("isSecureContext", true);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn() } });
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const pending = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  mocks.ws.state = "connected";
  mocks.audio.startCapture.mockReturnValueOnce(pending).mockResolvedValueOnce(undefined);
  const view = render(<App />);
  fireEvent.click(screen.getByText("Start"));
  mocks.ws.state = "disconnected";
  view.rerender(<App />);
  mocks.ws.state = "connected";
  view.rerender(<App />);
  await act(async () => { fireEvent.click(screen.getByText("Start")); });
  expect(screen.getByText("Talking")).toBeVisible();
  const stops = mocks.audio.stopCapture.mock.calls.length;
  const states = mocks.ws.setState.mock.calls.length;
  const starts = mocks.ws.sendVoiceStart.mock.calls.length;
  await act(async () => {
    if (outcome === "reject") reject(new Error("late permission denial")); else resolve();
    await pending.catch(() => {});
  });
  expect(mocks.audio.stopCapture).toHaveBeenCalledTimes(stops);
  expect(mocks.ws.setState).toHaveBeenCalledTimes(states);
  expect(mocks.ws.sendVoiceStart).toHaveBeenCalledTimes(starts);
  expect(screen.getByText("Talking")).toBeVisible();
});
