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
 * Resample a 16-bit signed LE PCM buffer using linear interpolation.
 * Returns the input unchanged when fromRate === toRate.
 */
function resamplePcm(buf: Buffer, fromRate: number, toRate: number): Buffer {
  if (fromRate === toRate) return buf;
  const srcSamples = buf.length >> 1;
  const dstSamples = Math.round(srcSamples * toRate / fromRate);
  const out = Buffer.alloc(dstSamples * 2);
  for (let i = 0; i < dstSamples; i++) {
    const pos = i * fromRate / toRate;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const s0 = buf.readInt16LE(idx * 2);
    const s1 = idx + 1 < srcSamples ? buf.readInt16LE((idx + 1) * 2) : s0;
    out.writeInt16LE(Math.round(s0 * (1 - frac) + s1 * frac), i * 2);
  }
  return out;
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
          yield resamplePcm(chunk, piperRate, targetRate);
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
