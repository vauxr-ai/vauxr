import * as net from "node:net";
import { getConfig } from "./config.js";
import { parseWyomingEvents } from "./wyoming-stt.js";

interface WyomingEvent {
  type: string;
  data: Record<string, unknown>;
  payload?: Buffer;
}

function encodeEvent(event: WyomingEvent): Buffer {
  const json: Record<string, unknown> = {
    type: event.type,
    data: event.data,
  };
  if (event.payload && event.payload.length > 0) {
    json.payload_length = event.payload.length;
  }
  const line = Buffer.from(JSON.stringify(json) + "\n", "utf-8");

  if (event.payload && event.payload.length > 0) {
    return Buffer.concat([line, event.payload]);
  }
  return line;
}

/**
 * Create a biquad low-pass filter section. Returns a function that
 * filters one sample at a time, carrying state across calls.
 */
function createBiquadLPF(cutoffHz: number, sampleRate: number, Q: number) {
  const w0 = (2 * Math.PI * cutoffHz) / sampleRate;
  const sinW0 = Math.sin(w0);
  const cosW0 = Math.cos(w0);
  const alpha = sinW0 / (2 * Q);
  const a0 = 1 + alpha;
  const b0 = ((1 - cosW0) / 2) / a0;
  const b1 = (1 - cosW0) / a0;
  const b2 = b0;
  const a1 = (-2 * cosW0) / a0;
  const a2 = (1 - alpha) / a0;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;

  return (x0: number): number => {
    const y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1; x1 = x0;
    y2 = y1; y1 = y0;
    return y0;
  };
}

/**
 * Stateful resampler: 4th-order Butterworth low-pass (two cascaded
 * biquads) + linear interpolation. The steeper rolloff eliminates
 * aliasing artifacts on sibilant sounds when downsampling.
 * State is carried across chunks for seamless audio.
 */
function createResampler(fromRate: number, toRate: number) {
  const cutoff = (toRate / 2) * 0.9;
  // 4th-order Butterworth = two biquad sections with specific Q values
  const lpf1 = createBiquadLPF(cutoff, fromRate, 0.54119610);  // 1st section Q
  const lpf2 = createBiquadLPF(cutoff, fromRate, 1.3065630);   // 2nd section Q

  return function resample(buf: Buffer): Buffer {
    const srcSamples = buf.length >> 1;

    // Apply cascaded biquad low-pass (working in float)
    const filtered = new Float64Array(srcSamples);
    for (let i = 0; i < srcSamples; i++) {
      filtered[i] = lpf2(lpf1(buf.readInt16LE(i * 2)));
    }

    // Linear interpolation downsample
    const dstSamples = Math.round(srcSamples * toRate / fromRate);
    const out = Buffer.alloc(dstSamples * 2);
    for (let i = 0; i < dstSamples; i++) {
      const pos = i * fromRate / toRate;
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const s0 = filtered[idx];
      const s1 = idx + 1 < srcSamples ? filtered[idx + 1] : s0;
      const val = Math.round(s0 * (1 - frac) + s1 * frac);
      out.writeInt16LE(Math.max(-32768, Math.min(32767, val)), i * 2);
    }
    return out;
  };
}

export interface SynthesizeOptions {
  targetRate?: number;
  signal?: AbortSignal;
  onSampleRate?: (rate: number) => void;
}

export async function* synthesize(text: string, opts?: SynthesizeOptions): AsyncGenerator<Buffer> {
  const targetRate = opts?.targetRate;
  const signal = opts?.signal;
  const onSampleRate = opts?.onSampleRate;
  const config = getConfig();
  const { host, port } = config.piper;

  const socket = net.createConnection({ host, port });
  const chunks: Buffer[] = [];
  let done = false;
  let error: Error | null = null;
  let resolveWait: (() => void) | null = null;
  let piperRate = 0; // detected from audio-start event
  let resample: ((buf: Buffer) => Buffer) | null = null;

  function notifyWait() {
    if (resolveWait) {
      const r = resolveWait;
      resolveWait = null;
      r();
    }
  }

  let buf: Uint8Array = Buffer.alloc(0);

  socket.on("data", (data: Buffer) => {
    buf = Buffer.concat([buf as Buffer, data]);
    const result = parseWyomingEvents(buf);
    buf = result.remainder;

    for (const ev of result.events) {
      if (ev.type === "audio-start" && typeof ev.data.rate === "number") {
        piperRate = ev.data.rate;
      } else if (ev.type === "audio-chunk") {
        if (piperRate === 0 && typeof ev.data.rate === "number") {
          piperRate = ev.data.rate;
        }
        if (ev.payload) {
          chunks.push(ev.payload);
        }
      } else if (ev.type === "audio-stop") {
        done = true;
      }
    }
    notifyWait();
  });

  socket.on("error", (err) => {
    error = new Error(`TTS connection error: ${err.message}`);
    done = true;
    notifyWait();
  });

  socket.on("close", () => {
    done = true;
    notifyWait();
  });

  await new Promise<void>((resolve, reject) => {
    socket.on("connect", () => {
      socket.write(encodeEvent({
        type: "synthesize",
        data: { text, voice: { name: config.piper.voice } },
      }));
      resolve();
    });
    socket.on("error", reject);
  });

  try {
    while (!done || chunks.length > 0) {
      if (signal?.aborted) {
        socket.destroy();
        return;
      }

      if (chunks.length > 0) {
        const chunk = chunks.shift()!;
        if (onSampleRate && piperRate) {
          onSampleRate(targetRate && targetRate !== piperRate ? targetRate : piperRate);
          // Only fire once
          opts!.onSampleRate = undefined;
        }
        if (targetRate && piperRate && piperRate !== targetRate) {
          if (!resample) resample = createResampler(piperRate, targetRate);
          yield resample(chunk);
        } else {
          yield chunk;
        }
      } else if (!done) {
        await new Promise<void>((r) => {
          resolveWait = r;
        });
      }

      if (error) throw error;
    }
  } finally {
    socket.destroy();
  }
}
