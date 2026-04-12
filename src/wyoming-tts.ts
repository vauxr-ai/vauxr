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

export async function* synthesize(text: string, signal?: AbortSignal): AsyncGenerator<Buffer> {
  const config = getConfig();
  const { host, port } = config.piper;

  const socket = net.createConnection({ host, port });
  const chunks: Buffer[] = [];
  let done = false;
  let error: Error | null = null;
  let resolveWait: (() => void) | null = null;

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
      if (ev.type === "audio-chunk" && ev.payload) {
        chunks.push(ev.payload);
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
        yield chunks.shift()!;
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
