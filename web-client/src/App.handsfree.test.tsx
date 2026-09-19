import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

const mocks = vi.hoisted(() => ({
  pcm: undefined as undefined | ((pcm: Int16Array) => void),
  control: {} as any,
  audio: { startCapture: vi.fn(), stopCapture: vi.fn(), stopPlayback: vi.fn(), queuePlayback: vi.fn(), setPlaybackRate: vi.fn(), resetPlayback: vi.fn() },
  ws: { state: "connected", connect: vi.fn(), disconnect: vi.fn(), sendVoiceStart: vi.fn(), sendAudioFrame: vi.fn(), sendJson: vi.fn(), setState: vi.fn(), addLog: vi.fn(), log: [] },
  request: vi.fn(),
}));
vi.mock("./auth/OwnerGate", () => ({ default: ({ children }: any) => children }));
vi.mock("./auth/api", () => ({ ownerFetch: (...args: any[]) => mocks.request(...args) }));
vi.mock("./hooks/useAudio", () => ({ useAudio: (opts: any) => { mocks.pcm = opts.onPcmChunk; return mocks.audio; } }));
vi.mock("./hooks/useWebSocket", () => ({ useWebSocket: (opts: any) => { mocks.control = opts; return mocks.ws; } }));
vi.mock("./components/Layout", () => ({ default: ({ main, talk }: any) => <>{main}{talk}</> }));
vi.mock("./components/ResizableSplit", () => ({ default: ({ top }: any) => top }));
vi.mock("./components/AccessPanel", () => ({ default: () => null }));
vi.mock("./components/ConfigPanel", () => ({ default: ({ onConnect }: any) => <button onClick={() => onConnect("ws://localhost/ws", "opaque-id", "credential")}>Connect</button> }));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.ws.state = "connected";
  mocks.audio.startCapture.mockResolvedValue(undefined);
  mocks.request.mockResolvedValue({ ok: true, json: async () => ({ voice: { mode: "standard" } }) });
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: vi.fn() });
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia: vi.fn() } });
});
async function setup() {
  const view = render(<App />);
  fireEvent.click(screen.getByText("Connect"));
  await waitFor(() => expect(screen.getByRole("radio", { name: "Standard hands-free" })).toBeEnabled());
  fireEvent.click(screen.getByRole("radio", { name: "Standard hands-free" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Start listening" })).toBeEnabled());
  return view;
}
function chunks(value: number, count: number) {
  act(() => { for (let i = 0; i < count; i++) mocks.pcm!(new Int16Array(1600).fill(value)); });
}
it("segments successive turns, closes playback on barge-in and suppresses stale response PCM", async () => {
  await setup();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Start listening" })); });
  chunks(0, 30);
  expect(mocks.ws.sendVoiceStart).not.toHaveBeenCalled();
  chunks(2000, 2);
  expect(mocks.ws.sendVoiceStart).toHaveBeenCalledTimes(1);
  act(() => mocks.control.onReady());
  chunks(0, 7);
  expect(mocks.ws.sendJson).toHaveBeenCalledWith({ type: "voice.end" });
  act(() => { mocks.control.onAudioStart(16000); mocks.control.onAudioFrame(new ArrayBuffer(3200)); });
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(1);
  const stops = mocks.audio.stopPlayback.mock.calls.length;
  chunks(2000, 2);
  expect(mocks.ws.sendVoiceStart).toHaveBeenCalledTimes(2);
  expect(mocks.audio.stopPlayback).toHaveBeenCalledTimes(stops + 1);
  act(() => { mocks.control.onAudioStart(16000); mocks.control.onAudioFrame(new ArrayBuffer(3200)); });
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(1);
  expect(mocks.audio.startCapture).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Stop listening" }));
  const frames = mocks.ws.sendAudioFrame.mock.calls.length;
  chunks(2000, 4);
  expect(mocks.ws.sendAudioFrame).toHaveBeenCalledTimes(frames);
  expect(mocks.ws.sendJson).toHaveBeenLastCalledWith({ type: "voice.abort" });
});
it.each(["mode", "disconnect", "logout", "stop"])("cancels permission acquisition on %s without starting a late turn", async reason => {
  const view = await setup();
  let resolve!: () => void;
  mocks.audio.startCapture.mockImplementationOnce(() => new Promise<void>(yes => { resolve = yes; }));
  fireEvent.click(screen.getByRole("button", { name: "Start listening" }));
  await act(async () => {
    if (reason === "mode") fireEvent.click(screen.getByRole("radio", { name: "Realtime" }));
    if (reason === "disconnect") { mocks.ws.state = "disconnected"; view.rerender(<App />); }
    if (reason === "logout") window.dispatchEvent(new Event("voice-stop"));
    if (reason === "stop") fireEvent.click(screen.getByRole("button", { name: "Stop listening" }));
  });
  await act(async () => resolve());
  chunks(2000, 3);
  expect(mocks.ws.sendVoiceStart).not.toHaveBeenCalled();
  expect(mocks.ws.sendAudioFrame).not.toHaveBeenCalled();
});
it("patches only mode and leaves the selected mode unchanged on API failure", async () => {
  await setup();
  mocks.request.mockResolvedValueOnce({ ok: false });
  fireEvent.click(screen.getByRole("radio", { name: "Realtime" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("radio", { name: "Standard hands-free" })).toHaveAttribute("aria-checked", "true");
  expect(JSON.parse(mocks.request.mock.calls.at(-1)![1].body)).toEqual({ mode: "realtime" });
});
