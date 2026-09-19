// Real Chromium Opus/RTP and the production hook, with a synthetic local sender.
// No provider traffic, microphone, recordings, traces or screenshots.
import { test, expect, login } from "./auth-fixture";

test("realtime playback survives transcript overlap and restart with bounded diagnostics", async ({ page, server }) => {
  test.setTimeout(60000);
  await page.addInitScript(() => {
    const state = { peers: [] as RTCPeerConnection[], contexts: [] as AudioContext[] };
    (window as any).loopback = state;
    navigator.mediaDevices.getUserMedia = async () => {
      const ctx = new AudioContext(); state.contexts.push(ctx);
      await ctx.resume();
      return ctx.createMediaStreamDestination().stream;
    };
    (window as any).answerLoopback = async (offer: RTCSessionDescriptionInit) => {
      const pc = new RTCPeerConnection(); state.peers.push(pc);
      const ctx = new AudioContext(); state.contexts.push(ctx);
      const tone = ctx.createOscillator(); tone.frequency.value = 440;
      const gain = ctx.createGain(); gain.gain.value = .1;
      const destination = ctx.createMediaStreamDestination();
      tone.connect(gain).connect(destination); tone.start(); await ctx.resume();
      pc.addTrack(destination.stream.getAudioTracks()[0], destination.stream);
      pc.ondatachannel = e => { e.channel.onmessage = () => {}; };
      await pc.setRemoteDescription(offer);
      await pc.setLocalDescription(await pc.createAnswer());
      if (pc.iceGatheringState !== "complete") await new Promise<void>(resolve => {
        pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === "complete") resolve(); };
      });
      return { type: pc.localDescription!.type, sdp: pc.localDescription!.sdp };
    };
  });
  let socket: any;
  await page.routeWebSocket(/\/ws$/, ws => {
    socket = ws;
    ws.onMessage(raw => {
      if (typeof raw !== "string") return;
      const message = JSON.parse(raw);
      if (message.type === "hello") ws.send(JSON.stringify({ type: "hello" }));
      if (message.type === "realtime.start") ws.send(JSON.stringify({ type: "realtime.armed" }));
    });
  });
  await page.route("**/api/offer", async route => {
    const body = route.request().postDataJSON();
    const answer = await page.evaluate(offer => (window as any).answerLoopback(offer), { type: "offer", sdp: body.sdp });
    await route.fulfill({ json: answer });
    socket.send(JSON.stringify({ type: "realtime.ready" }));
  });
  await login(page, server);
  await page.getByRole("button", { name: "Connect browser voice", exact: true }).click();
  const talk = page.getByRole("complementary", { name: "Talk panel" });
  await talk.getByRole("radio", { name: "Realtime", exact: true }).click();
  await page.evaluate(() => (window as any).vauxrRealtimeDiagnostics.enable());
  const snapshot = () => page.evaluate(() => (window as any).vauxrRealtimeDiagnostics.snapshot().entries);
  for (let cycle = 0; cycle < 2; cycle++) {
    await page.evaluate(() => (window as any).vauxrRealtimeDiagnostics.clear());
    await talk.getByRole("button", { name: "Start listening", exact: true }).click();
    await expect.poll(async () => (await snapshot()).some((e: any) => e.event === "playback.playing")).toBe(true);
    await expect.poll(async () => (await snapshot()).filter((e: any) => e.event === "inbound-rtp").length).toBeGreaterThanOrEqual(2);
    for (let i = 0; i < 20; i++) socket.send(JSON.stringify({ type: "realtime.transcript", role: i % 2 ? "user" : "assistant", turn_id: `synthetic-${i % 2}`, text: `synthetic overlap ${i}` }));
    await expect.poll(async () => (await snapshot()).filter((e: any) => e.event === "inbound-rtp").length).toBeGreaterThanOrEqual(4);
    const entries = await snapshot();
    const inbound = entries.filter((e: any) => e.event === "inbound-rtp");
    expect(inbound.at(-1).packetsReceived).toBeGreaterThan(inbound[0].packetsReceived);
    expect(inbound.some((e: any) => e.audioLevel > 0)).toBe(true);
    const samples = entries.filter((e: any) => e.event === "sample");
    expect(samples.at(-1).currentTime).toBeGreaterThan(samples[0].currentTime);
    expect(entries.filter((e: any) => e.event === "play.request")).toHaveLength(1);
    expect(entries.some((e: any) => ["play.rejected", "playback.stalled", "playback.error"].includes(e.event))).toBe(false);
    console.log(`Loopback cycle ${cycle + 1}: packets ${inbound[0].packetsReceived}->${inbound.at(-1).packetsReceived}, lost=${inbound.at(-1).packetsLost}, jitter=${inbound.at(-1).jitter}, concealed=${inbound.at(-1).concealedSamples}`);
    await talk.getByRole("button", { name: "Stop listening", exact: true }).click();
    const stopped = await snapshot();
    await page.waitForTimeout(1200);
    expect(await snapshot()).toEqual(stopped);
    await page.evaluate(async () => {
      for (const pc of (window as any).loopback.peers) pc.close();
      for (const ctx of (window as any).loopback.contexts) await ctx.close();
      (window as any).loopback.peers = []; (window as any).loopback.contexts = [];
    });
  }
});
