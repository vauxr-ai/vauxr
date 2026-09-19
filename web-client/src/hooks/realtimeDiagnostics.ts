// Explicit opt-in, memory only. Never retain raw stats, track IDs, SDP or content.
type Entry = { ms: number; event: string; session: number; [key: string]: number | string | boolean };
const LIMIT = 300;
let enabled = false;
let origin = performance.now();
let serial = 0;
let epoch = 0;
const entries: Entry[] = [];
export const realtimeDiagnostics = {
  enable() { enabled = true; this.clear(); },
  disable() { enabled = false; this.clear(); },
  clear() { epoch++; entries.length = 0; origin = performance.now(); },
  snapshot() { return { enabled, limit: LIMIT, entries: entries.map(entry => ({ ...entry })) }; },
};
declare global { interface Window { vauxrRealtimeDiagnostics: typeof realtimeDiagnostics } }
window.vauxrRealtimeDiagnostics = realtimeDiagnostics;

const fields = ["packetsReceived", "packetsLost", "jitter", "bytesReceived", "audioLevel",
  "totalAudioEnergy", "totalSamplesDuration", "concealedSamples", "silentConcealedSamples",
  "concealmentEvents", "totalSamplesReceived", "jitterBufferDelay", "jitterBufferEmittedCount",
  "jitterBufferTargetDelay", "insertedSamplesForDeceleration", "removedSamplesForAcceleration"] as const;

export function observePlayback(pc: RTCPeerConnection, audio: HTMLAudioElement) {
  const session = ++serial;
  let active = true;
  let pending = false;
  const removers: (() => void)[] = [];
  const record = (event: string, values: Record<string, number | string | boolean> = {}) => {
    if (!enabled || !active) return;
    entries.push({ ms: Math.round(performance.now() - origin), session, event, ...values });
    if (entries.length > LIMIT) entries.splice(0, entries.length - LIMIT);
  };
  const playback = () => ({ currentTime: audio.currentTime, paused: audio.paused,
    readyState: audio.readyState, networkState: audio.networkState, muted: audio.muted,
    volume: audio.volume, errorCode: audio.error?.code ?? 0 });
  const listen = (target: EventTarget, name: string, fn: () => void) => {
    target.addEventListener(name, fn);
    removers.push(() => target.removeEventListener(name, fn));
  };
  for (const name of ["playing", "waiting", "stalled", "pause", "ended", "emptied", "error", "volumechange", "loadedmetadata"]) {
    listen(audio, name, () => record(`playback.${name}`, playback()));
  }
  for (const name of ["connectionstatechange", "iceconnectionstatechange"]) {
    listen(pc, name, () => record(name, { connection: pc.connectionState, ice: pc.iceConnectionState }));
  }
  listen(document, "visibilitychange", () => record("visibility", { hidden: document.hidden }));
  const tracks = new Set<MediaStreamTrack>();
  record("start");
  const interval = setInterval(async () => {
    if (!enabled || !active || pending) return;
    pending = true;
    const sampledEpoch = epoch;
    record("sample", { ...playback(), hidden: document.hidden, connection: pc.connectionState, ice: pc.iceConnectionState });
    try {
      const stats = await pc.getStats();
      if (sampledEpoch !== epoch) return;
      let count = 0;
      stats.forEach(stat => {
        if (stat.type !== "inbound-rtp" || (stat.kind ?? stat.mediaType) !== "audio" || count >= 4) return;
        const values: Record<string, number> = { inbound: count++ };
        for (const field of fields) {
          if (typeof stat[field] === "number" && Number.isFinite(stat[field])) values[field] = stat[field];
        }
        record("inbound-rtp", values);
      });
    } catch { if (sampledEpoch === epoch) record("stats.error"); }
    finally { pending = false; }
  }, 1000);
  return {
    record,
    track(track: MediaStreamTrack) {
      record("track", { audio: track.kind === "audio", muted: track.muted, ended: track.readyState === "ended" });
      if (tracks.has(track) || tracks.size >= 4) return;
      tracks.add(track);
      for (const name of ["mute", "unmute", "ended"]) listen(track, name, () => record(`track.${name}`));
    },
    stop() {
      record("stop", playback()); active = false; clearInterval(interval);
      removers.forEach(remove => remove()); tracks.clear();
    },
  };
}
