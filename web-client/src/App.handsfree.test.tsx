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
  expect(mocks.ws.sendJson).toHaveBeenLastCalledWith({ type: "abort" });
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

function responseAudio() {
  act(() => {
    mocks.control.onAudioStart(16000);
    mocks.control.onAudioFrame(new ArrayBuffer(3200));
    mocks.control.onAudioEnd(true);
  });
}
async function respond() {
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Start listening" })); });
  chunks(2000, 2);
  act(() => mocks.control.onReady());
  chunks(0, 7);
  act(() => { mocks.control.onAudioStart(16000); mocks.control.onAudioFrame(new ArrayBuffer(3200)); });
}
it.each(["stop", "mode", "interrupt"])("%s cancels a response and rejects stale start/frame/end", async reason => {
  const view = await setup();
  await respond();
  if (reason === "interrupt") { mocks.ws.state = "speaking"; view.rerender(<App />); }
  await act(async () => {
    if (reason === "stop") fireEvent.click(screen.getByRole("button", { name: "Stop listening" }));
    if (reason === "mode") fireEvent.click(screen.getByRole("radio", { name: "Realtime" }));
    if (reason === "interrupt") fireEvent.click(screen.getByRole("button", { name: "Interrupt" }));
  });
  expect(mocks.ws.sendJson).toHaveBeenLastCalledWith({ type: "abort" });
  const frames = mocks.audio.queuePlayback.mock.calls.length;
  const resets = mocks.audio.resetPlayback.mock.calls.length;
  responseAudio();
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(frames);
  expect(mocks.audio.resetPlayback).toHaveBeenCalledTimes(resets);
});
it("keeps the ready barrier through replacement endpointing and requires a fresh audio.start", async () => {
  await setup();
  await respond();
  chunks(2000, 2); // barge-in, still waiting for the server's ordered ready
  chunks(0, 7);
  const frames = mocks.audio.queuePlayback.mock.calls.length;
  responseAudio(); // old response before ready must not open the playback gate
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(frames);
  act(() => { mocks.control.onReady(); mocks.control.onAudioFrame(new ArrayBuffer(3200)); });
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(frames);
  responseAudio(); // fresh response after ready
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(frames + 1);
  act(() => mocks.control.onAudioFrame(new ArrayBuffer(3200)));
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(frames + 1);
});
it.each(["SPEECH_UNAVAILABLE", "PIPELINE_ERROR", "STT_ERROR", "TTS_ERROR", "NO_AGENT"])("%s stops capture and permits an explicit retry", async code => {
  await setup();
  await respond();
  act(() => mocks.control.onError(code, "Turn failed"));
  expect(screen.getByRole("alert")).toHaveTextContent("Turn failed");
  expect(screen.getByRole("button", { name: "Start listening" })).toBeEnabled();
  expect(mocks.ws.sendJson).toHaveBeenLastCalledWith({ type: "abort" });
  expect(mocks.ws.setState).toHaveBeenLastCalledWith("connected");
  const frames = mocks.ws.sendAudioFrame.mock.calls.length;
  chunks(2000, 3);
  expect(mocks.ws.sendAudioFrame).toHaveBeenCalledTimes(frames);
  const played = mocks.audio.queuePlayback.mock.calls.length;
  responseAudio();
  expect(mocks.audio.queuePlayback).toHaveBeenCalledTimes(played);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Start listening" })); });
  expect(screen.getByRole("button", { name: "Stop listening" })).toBeEnabled();
});
it("ignores unrelated device settings and preserves capture on unchanged effective mode", async () => {
  await setup();
  await respond();
  const calls = mocks.request.mock.calls.length;
  const stops = mocks.audio.stopCapture.mock.calls.length;
  const changed = (detail: object) => window.dispatchEvent(new CustomEvent("speech-settings-changed", { detail }));
  await act(async () => { changed({ scope: "device", deviceId: "other-device" }); });
  expect(mocks.request).toHaveBeenCalledTimes(calls);
  await act(async () => { changed({ scope: "global" }); });
  await act(async () => { changed({ scope: "device", deviceId: "opaque-id" }); });
  expect(mocks.audio.stopCapture).toHaveBeenCalledTimes(stops);
  expect(screen.getByRole("button", { name: "Stop listening" })).toBeEnabled();
  mocks.request.mockResolvedValueOnce({ ok: true, json: async () => ({ voice: { mode: "realtime" } }) });
  await act(async () => { changed({ scope: "global" }); });
  expect(mocks.audio.stopCapture).toHaveBeenCalledTimes(stops + 1);
  expect(screen.getByRole("radio", { name: "Realtime" })).toHaveAttribute("aria-checked", "true");
});
