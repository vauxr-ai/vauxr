import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
// Synthetic oscillator input; real Chromium AudioContext, worklet and PCM conversion.
// Local WS response stub avoids any speech inference or paid provider.
import { test, expect, login, seedRealtimeAgent } from "./auth-fixture";

// Optional authenticated state captures; automatic auth snapshots remain disabled.
async function capture(page: Page, state: string) {
  const directory = process.env.VAUXR_B2_ARTIFACT_DIR;
  if (!directory) return;
  mkdirSync(directory, { recursive: true });
  await page.screenshot({ path: resolve(directory, `voice-${state}.png`) });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const state = { gain: null as GainNode | null, tracks: [] as MediaStreamTrack[], contexts: [] as AudioContext[], all: [] as AudioContext[] };
    const Original = window.AudioContext;
    window.AudioContext = class extends Original {
      constructor(options?: AudioContextOptions) { super(options); state.all.push(this); }
    };
    (window as any).voiceTest = state;
    navigator.mediaDevices.getUserMedia = async () => {
      const ctx = new AudioContext({ sampleRate: 16000 });
      state.contexts.push(ctx);
      const oscillator = ctx.createOscillator(); oscillator.frequency.value = 220;
      const gain = ctx.createGain(); gain.gain.value = 0; state.gain = gain;
      const destination = ctx.createMediaStreamDestination();
      oscillator.connect(gain).connect(destination); oscillator.start(); await ctx.resume();
      state.tracks.push(...destination.stream.getTracks());
      return destination.stream;
    };
  });
});

test("Talk segments real worklet PCM, barges in, persists modes and keeps button geometry", async ({ page, server }) => {
  test.setTimeout(60000);
  const messages: string[] = [];
  let frames = 0;
  let nonzeroPcm = false;
  let deviceId = "";
  let socket: any;
  await page.routeWebSocket(/\/ws$/, ws => {
    socket = ws;
    ws.onMessage(message => {
      if (typeof message !== "string") { frames++; nonzeroPcm ||= Buffer.from(message).subarray(3).some(v => v !== 0); return; }
      const msg = JSON.parse(message); messages.push(msg.type);
      if (msg.type === "hello") { deviceId = msg.device_id; ws.send(JSON.stringify({ type: "hello" })); }
      if (msg.type === "voice.start") ws.send(JSON.stringify({ type: "ready" }));
    });
  });
  seedRealtimeAgent(server);
  await server.restart();
  await login(page, server);
  await page.getByRole("button", { name: "Connect browser", exact: true }).click();
  const talk = page.getByRole("complementary", { name: "Talk panel" });
  await expect(talk.getByRole("radio", { name: "Standard push-to-talk" })).toBeEnabled();
  const hold = talk.getByRole("button", { name: "Hold to Talk", exact: true });
  const box = await hold.boundingBox();
  expect(box?.width).toBe(112); expect(box?.height).toBe(112);
  expect(await talk.getByRole("combobox").count()).toBe(0);
  await talk.getByRole("radio", { name: "Standard hands-free" }).click();
  const start = talk.getByRole("button", { name: "Start listening", exact: true });
  await expect(start).toBeEnabled();
  expect((await start.boundingBox())?.width).toBe(box?.width);
  await start.click();
  await expect(talk.getByRole("button", { name: "Stop listening" })).toBeEnabled();
  expect((await talk.getByRole("button", { name: "Stop listening" }).boundingBox())?.width).toBe(box?.width);
  const tone = (value: number) => page.evaluate(v => { (window as any).voiceTest.gain.gain.value = v; }, value);
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Keep real capture active while navigating the companion's phone compositions.
  await expect.poll(() => page.evaluate(() => !!(window as any).voiceTest.gain)).toBe(true);
  for (const theme of ["dark", "light"]) {
    await page.getByLabel("Appearance").selectOption(theme);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".companion-header [role=status]")).toHaveText("Listening");
    await capture(page, `phone-${theme}-listening`);
    await page.keyboard.press("Escape");
    await expect(page.locator(".companion-header [role=status]")).toHaveText("Listening");
    await expect(page.getByRole("button", { name: "Stop listening", exact: true })).toBeVisible();
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.getByRole("button", { name: "Expand voice companion" }).click();
  }
  await talk.getByRole("button", { name: "Mute output" }).click();
  await expect(page.locator(".companion-header [role=status]")).toHaveText("Listening · Output muted");
  await talk.getByRole("button", { name: "Unmute output" }).click();

  await expect.poll(() => page.evaluate(() => !!(window as any).voiceTest.gain)).toBe(true);
  await tone(0.2);
  await expect.poll(() => messages.filter(x => x === "voice.start").length).toBe(1);
  await capture(page, "listening");
  await expect.poll(() => frames).toBeGreaterThan(2);
  await tone(0);
  await expect.poll(() => messages.filter(x => x === "voice.end").length).toBe(1);
  await expect(page.locator(".companion-header [role=status]")).toHaveText("Thinking");
  await capture(page, "thinking");
  socket.send(JSON.stringify({ type: "audio.start", sample_rate: 16000 }));
  const playback = Buffer.alloc(32003); playback[0] = 2;
  socket.send(playback);
  await expect(page.locator(".companion-header [role=status]")).toHaveText("Speaking");
  await capture(page, "speaking");
  expect(nonzeroPcm).toBe(true);
  const playbackIndex = await page.evaluate(() => (window as any).voiceTest.all.length - 1);
  await tone(0.2);
  await expect.poll(() => messages.filter(x => x === "voice.start").length).toBe(2);
  expect(messages.slice(-2)).toEqual(["abort", "voice.start"]);
  await expect.poll(() => page.evaluate(i => (window as any).voiceTest.all[i].state, playbackIndex)).toBe("closed");
  expect(await page.evaluate(() => (window as any).voiceTest.tracks[0].readyState)).toBe("live");
  await tone(0);
  await expect.poll(() => messages.filter(x => x === "voice.end").length).toBe(2);
  await talk.getByRole("radio", { name: "Realtime", exact: true }).click();
  await expect(start).toBeEnabled();
  expect((await start.boundingBox())?.width).toBe(box?.width);
  await expect.poll(() => page.evaluate(() => (window as any).voiceTest.tracks[0].readyState)).toBe("ended");
  // Realtime uses the same button and real browser media acquisition. The
  // local control stub deliberately never arms a provider session.
  await start.click();
  await expect.poll(() => messages.includes("realtime.start")).toBe(true);
  expect((await talk.getByRole("button", { name: "Stop listening" }).boundingBox())?.width).toBe(box?.width);
  await talk.getByRole("button", { name: "Stop listening" }).click();
  await expect.poll(() => messages.includes("realtime.stop")).toBe(true);
  await expect.poll(() => page.evaluate(() => (window as any).voiceTest.tracks[1].readyState)).toBe("ended");
  // Persist distinct per-mode voices through the real owner API.
  const standardVoice = await page.evaluate(async id => {
    const settings = await (await fetch(`/api/devices/${id}/speech`)).json();
    const voice = settings.backends.find((backend: any) => backend.id === "piper").voices[0];
    const session = await (await fetch("/api/auth/session")).json();
    const response = await fetch(`/api/devices/${id}/speech`, { method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
      body: JSON.stringify({ realtime_voice: "cedar", voices: { piper: voice } }) });
    if (!response.ok) throw new Error(`Voice patch failed ${response.status}`);
    return voice;
  }, deviceId);
  await page.reload();
  await page.getByRole("button", { name: "Connect browser", exact: true }).click();
  await expect(talk.getByRole("radio", { name: "Realtime", exact: true })).toHaveAttribute("aria-checked", "true");
  await expect(start).toBeEnabled();
  await talk.getByRole("radio", { name: "Standard hands-free" }).click();
  await expect(start).toBeEnabled();
  const saved = await page.evaluate(async id => (await fetch(`/api/devices/${id}/speech`)).json(), deviceId);
  expect(saved.voice.mode).toBe("standard");
  expect(saved.voice.realtime_voice).toBe("cedar");
  expect(saved.overrides.voices.piper).toBe(standardVoice);
  await page.route("**/api/devices", route => route.fulfill({ json: [{
    id: deviceId, name: "Saved browser display name", state: "idle", lastSeen: new Date().toISOString(), config: {},
  }] }));
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  await page.getByText("Saved browser display name", { exact: true }).click();
  const settings = page.getByRole("region", { name: "Assigned agent", exact: true });
  await settings.getByRole("button", { name: "Override agent voice pipeline defaults", exact: true }).click();
  await expect(settings.getByRole("combobox", { name: "Voice", exact: true })).toHaveValue(standardVoice);
  await talk.getByRole("radio", { name: "Realtime", exact: true }).click();
  await expect(settings.getByRole("combobox", { name: "Realtime voice", exact: true })).toHaveValue("cedar");
  await settings.getByLabel("Voice mode").selectOption("standard");
  await expect(talk.getByRole("radio", { name: "Standard hands-free" })).toHaveAttribute("aria-checked", "true");
  await expect(settings.getByRole("combobox", { name: "Voice", exact: true })).toHaveValue(standardVoice);
  await start.click();
  await expect.poll(() => page.evaluate(() => (window as any).voiceTest.tracks.length)).toBe(1);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).voiceTest.tracks[0].readyState)).toBe("ended");
});


test("Standard failures release real capture and stale response messages cannot restart playback", async ({ page, server }) => {
  let socket: any;
  const messages: string[] = [];
  await page.routeWebSocket(/\/ws$/, ws => {
    socket = ws;
    ws.onMessage(message => {
      if (typeof message !== "string") return;
      const msg = JSON.parse(message); messages.push(msg.type);
      if (msg.type === "hello") ws.send(JSON.stringify({ type: "hello" }));
      if (msg.type === "voice.start") ws.send(JSON.stringify({ type: "ready" }));
    });
  });
  await login(page, server);
  await page.getByRole("button", { name: "Connect browser", exact: true }).click();
  const talk = page.getByRole("complementary", { name: "Talk panel" });
  await expect(talk.getByRole("radio", { name: "Standard hands-free" })).toBeEnabled();
  await talk.getByRole("radio", { name: "Standard hands-free" }).click();
  const start = talk.getByRole("button", { name: "Start listening", exact: true });
  const tone = (value: number) => page.evaluate(v => { (window as any).voiceTest.gain.gain.value = v; }, value);
  const response = () => {
    socket.send(JSON.stringify({ type: "audio.start", sample_rate: 16000 }));
    const pcm = Buffer.alloc(32003); pcm[0] = 2; socket.send(pcm);
    socket.send(JSON.stringify({ type: "audio.end", follow_up: true }));
  };
  for (const [index, code] of ["SPEECH_UNAVAILABLE", "PIPELINE_ERROR"].entries()) {
    await start.click();
    await expect.poll(() => page.evaluate(() => (window as any).voiceTest.tracks.length)).toBe(index + 1);
    await tone(0.2);
    await expect.poll(() => messages.filter(m => m === "voice.start").length).toBe(index + 1);
    if (code === "PIPELINE_ERROR") {
      await tone(0);
      await expect.poll(() => messages.includes("voice.end")).toBe(true);
    }
    socket.send(JSON.stringify({ type: "error", code, message: "Synthetic speech boundary failure" }));
    await expect(start).toBeEnabled();
    await expect.poll(() => page.evaluate(i => (window as any).voiceTest.tracks[i].readyState, index)).toBe("ended");
    await expect.poll(() => messages.at(-1)).toBe("abort");
    const contexts = await page.evaluate(() => (window as any).voiceTest.all.length);
    response();
    // A subsequent error is an ordered marker that the stale messages were handled.
    socket.send(JSON.stringify({ type: "error", code, message: "After stale response" }));
    await expect(page.getByRole("alert")).toHaveText("After stale response");
    expect(await page.evaluate(() => (window as any).voiceTest.all.length)).toBe(contexts);
    await expect(start).toBeEnabled();
  }
});
