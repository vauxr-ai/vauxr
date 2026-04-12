import * as net from "node:net";
import { getConfig } from "./config.js";

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

export function parseWyomingEvents(data: Uint8Array): { events: WyomingEvent[]; remainder: Buffer } {
  const buf = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  const events: WyomingEvent[] = [];
  let offset = 0;

  while (offset < buf.length) {
    const newlineIdx = buf.indexOf(0x0a, offset);
    if (newlineIdx === -1) break;

    const lineStart = offset;
    const line = buf.subarray(offset, newlineIdx).toString("utf-8");
    offset = newlineIdx + 1;

    let parsed: { type: string; data: Record<string, unknown>; data_length?: number; payload_length?: number };
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }

    // data_length = size of the data JSON object that follows the header
    const dataLen = parsed.data_length ?? 0;
    // payload_length = size of the binary payload after the data object
    const payloadLen = parsed.payload_length ?? 0;
    const totalTrailing = dataLen + payloadLen;

    if (totalTrailing > 0 && offset + totalTrailing > buf.length) {
      // Not enough data yet — rewind to before the header line
      offset = lineStart;
      break;
    }

    let data = parsed.data ?? {};
    if (dataLen > 0) {
      try {
        data = JSON.parse(buf.subarray(offset, offset + dataLen).toString("utf-8"));
      } catch { /* keep header data */ }
      offset += dataLen;
    }

    let payload: Buffer | undefined;
    if (payloadLen > 0) {
      payload = Buffer.from(buf.subarray(offset, offset + payloadLen));
      offset += payloadLen;
    }

    events.push({
      type: parsed.type,
      data,
      payload,
    });
  }

  return { events, remainder: Buffer.from(buf.subarray(offset)) };
}

export async function transcribe(chunks: Buffer[], sampleRate = 16000): Promise<string> {
  const { host, port } = getConfig().whisper;

  return new Promise<string>((resolve, reject) => {
    const socket = net.createConnection({ host, port }, () => {
      // Send audio-start
      socket.write(encodeEvent({
        type: "audio-start",
        data: { rate: sampleRate, width: 2, channels: 1 },
      }));

      // Send all audio chunks
      for (const chunk of chunks) {
        socket.write(encodeEvent({
          type: "audio-chunk",
          data: { rate: sampleRate, width: 2, channels: 1 },
          payload: chunk,
        }));
      }

      // Send audio-stop
      socket.write(encodeEvent({
        type: "audio-stop",
        data: {},
      }));
    });

    let buf: Uint8Array = Buffer.alloc(0);
    const timeout = setTimeout(() => {
      socket.destroy();
      reject(new Error("STT timeout after 30s"));
    }, 30_000);

    socket.on("data", (data: Buffer) => {
      buf = Buffer.concat([buf as Buffer, data]);
      const { events, remainder } = parseWyomingEvents(buf);
      buf = remainder;

      for (const ev of events) {
        if (ev.type === "transcript") {
          clearTimeout(timeout);
          const text = (ev.data.text as string) ?? "";
          socket.destroy();
          resolve(text);
          return;
        }
      }
    });

    socket.on("error", (err) => {
      clearTimeout(timeout);
      reject(new Error(`STT connection error: ${err.message}`));
    });

    socket.on("close", () => {
      clearTimeout(timeout);
    });
  });
}
