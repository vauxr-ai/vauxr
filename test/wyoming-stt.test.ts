import { describe, it, expect, afterEach } from "vitest";
import * as net from "node:net";
import { parseWyomingEvents } from "../src/wyoming-stt.js";

// Set env vars before importing transcribe
process.env.OPENCLAW_URL = "wss://test:18789";
process.env.OPENCLAW_TOKEN = "test-token";
process.env.DEVICE_TOKEN = "test-device-token";

function encodeWyomingEvent(
  type: string,
  data: Record<string, unknown>,
  payload?: Buffer,
): Buffer {
  const json: Record<string, unknown> = { type, data };
  if (payload && payload.length > 0) {
    json.payload_length = payload.length;
  }
  const jsonBuf = Buffer.from(JSON.stringify(json), "utf-8");
  const headerLen = Buffer.alloc(4);
  headerLen.writeUInt32BE(jsonBuf.length, 0);

  if (payload && payload.length > 0) {
    return Buffer.concat([headerLen, jsonBuf, payload]);
  }
  return Buffer.concat([headerLen, jsonBuf]);
}

describe("parseWyomingEvents", () => {
  it("parses a single event without payload", () => {
    const buf = encodeWyomingEvent("transcript", { text: "hello world" });
    const { events, remainder } = parseWyomingEvents(buf);
    expect(events).toHaveLength(1);
    expect(events[0]!.type).toBe("transcript");
    expect(events[0]!.data.text).toBe("hello world");
    expect(remainder.length).toBe(0);
  });

  it("parses a single event with payload", () => {
    const payload = Buffer.from([1, 2, 3, 4]);
    const buf = encodeWyomingEvent("audio-chunk", { rate: 16000 }, payload);
    const { events, remainder } = parseWyomingEvents(buf);
    expect(events).toHaveLength(1);
    expect(events[0]!.type).toBe("audio-chunk");
    expect(events[0]!.payload).toEqual(payload);
    expect(remainder.length).toBe(0);
  });

  it("parses multiple events", () => {
    const ev1 = encodeWyomingEvent("audio-start", { rate: 16000, width: 2, channels: 1 });
    const ev2 = encodeWyomingEvent("transcript", { text: "test" });
    const buf = Buffer.concat([ev1, ev2]);
    const { events, remainder } = parseWyomingEvents(buf);
    expect(events).toHaveLength(2);
    expect(events[0]!.type).toBe("audio-start");
    expect(events[1]!.type).toBe("transcript");
    expect(remainder.length).toBe(0);
  });

  it("handles partial data correctly", () => {
    const full = encodeWyomingEvent("transcript", { text: "hello" });
    const partial = full.subarray(0, full.length - 2);
    const { events, remainder } = parseWyomingEvents(partial);
    expect(events).toHaveLength(0);
    expect(remainder.length).toBe(partial.length);
  });
});

describe("transcribe (mock server)", () => {
  let server: net.Server | null = null;

  afterEach(() => {
    if (server) {
      server.close();
      server = null;
    }
  });

  it("sends correct Wyoming framing and receives transcript", async () => {
    const receivedEvents: Array<{ type: string; data: Record<string, unknown>; payloadLen: number }> = [];

    server = net.createServer((socket) => {
      let buf = Buffer.alloc(0);
      socket.on("data", (data) => {
        buf = Buffer.concat([buf, data]);
        const { events, remainder } = parseWyomingEvents(buf);
        buf = remainder;

        for (const ev of events) {
          receivedEvents.push({
            type: ev.type,
            data: ev.data,
            payloadLen: ev.payload?.length ?? 0,
          });

          // When we receive audio-stop, send back a transcript
          if (ev.type === "audio-stop") {
            socket.write(encodeWyomingEvent("transcript", { text: "hello world" }));
          }
        }
      });
    });

    const port = await new Promise<number>((resolve) => {
      server!.listen(0, "127.0.0.1", () => {
        const addr = server!.address() as net.AddressInfo;
        resolve(addr.port);
      });
    });

    // Override config for test
    process.env.WHISPER_URL = `tcp://127.0.0.1:${port}`;
    const { resetConfig } = await import("../src/config.js");
    resetConfig();

    const { transcribe } = await import("../src/wyoming-stt.js");
    const pcm = Buffer.alloc(3200); // 100ms of 16kHz 16-bit mono
    const result = await transcribe([pcm, pcm]);

    expect(result).toBe("hello world");
    expect(receivedEvents[0]!.type).toBe("audio-start");
    expect(receivedEvents[0]!.data.rate).toBe(16000);
    expect(receivedEvents[0]!.data.width).toBe(2);
    expect(receivedEvents[0]!.data.channels).toBe(1);
    expect(receivedEvents[1]!.type).toBe("audio-chunk");
    expect(receivedEvents[1]!.payloadLen).toBe(3200);
    expect(receivedEvents[2]!.type).toBe("audio-chunk");
    expect(receivedEvents[3]!.type).toBe("audio-stop");
  });
});
