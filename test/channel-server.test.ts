import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Set env vars before any imports
process.env.DEVICE_TOKEN = "test-device-token";
process.env.WHISPER_URL = "tcp://127.0.0.1:10300";
process.env.PIPER_URL = "tcp://127.0.0.1:10200";
process.env.OPENCLAW_URL = "";
process.env.OPENCLAW_TOKEN = "";

// Mock channel-registry
vi.mock("../src/channel-registry.js", () => ({
  getActive: vi.fn(),
  getAll: vi.fn(() => []),
  getById: vi.fn(),
  load: vi.fn(),
  create: vi.fn(),
  remove: vi.fn(),
  activate: vi.fn(),
  rotateToken: vi.fn(),
  validateChannelToken: vi.fn(),
}));

import { ChannelServer } from "../src/channel-server.js";
import * as channelRegistry from "../src/channel-registry.js";
import { resetConfig } from "../src/config.js";
import type { Channel } from "../src/channel-registry.js";
import { EventEmitter } from "node:events";

const mockValidateChannelToken = vi.mocked(channelRegistry.validateChannelToken);
const mockGetActive = vi.mocked(channelRegistry.getActive);

function makeChannel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: "ch-1",
    name: "Test Channel",
    type: "openclaw",
    tokenHash: "hashed",
    active: true,
    createdAt: new Date().toISOString(),
    ...overrides,
  };
}

interface MockWs extends EventEmitter {
  OPEN: number;
  readyState: number;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  _sent: Array<string | Buffer>;
}

function createMockWs(): MockWs {
  const ws = new EventEmitter() as MockWs;
  ws.OPEN = 1;
  ws.readyState = 1;
  ws._sent = [];
  ws.send = vi.fn((data: string | Buffer) => { ws._sent.push(data); });
  ws.close = vi.fn(() => {
    ws.readyState = 3;
    ws.emit("close");
  });
  return ws;
}

function createMockWss(): EventEmitter {
  return new EventEmitter();
}

function getSentJSON(ws: MockWs): Array<Record<string, unknown>> {
  return ws._sent
    .filter((m): m is string => typeof m === "string")
    .map((m) => JSON.parse(m) as Record<string, unknown>);
}

let channelServer: ChannelServer;
let wss: EventEmitter;

beforeEach(() => {
  vi.clearAllMocks();
  resetConfig();
  channelServer = new ChannelServer();
  wss = createMockWss();
  channelServer.attach(wss as never);
});

afterEach(() => {
  wss.removeAllListeners();
});

function connectChannel(ws: MockWs): void {
  // Simulate WSS connection event with /channel path
  wss.emit("connection", ws, { url: "/channel" });
}

describe("channel-server auth", () => {
  it("auth handshake with valid token — sends channel.ready", async () => {
    const channel = makeChannel();
    mockValidateChannelToken.mockResolvedValue(channel);

    const ws = createMockWs();
    connectChannel(ws);

    // Send auth message
    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "vx_ch_abc123" })));

    // validateChannelToken is async, wait a tick
    await vi.waitFor(() => {
      const msgs = getSentJSON(ws);
      expect(msgs.find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    const msgs = getSentJSON(ws);
    const ready = msgs.find((m) => m.type === "channel.ready");
    expect(ready!.channelId).toBe("ch-1");
    expect(ready!.name).toBe("Test Channel");
  });

  it("auth handshake with invalid token — sends error + closes connection", async () => {
    mockValidateChannelToken.mockResolvedValue(null);

    const ws = createMockWs();
    connectChannel(ws);

    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "bad-token" })));

    await vi.waitFor(() => {
      const msgs = getSentJSON(ws);
      expect(msgs.find((m) => m.type === "error" && m.code === "UNAUTHORIZED")).toBeTruthy();
    });

    expect(ws.close).toHaveBeenCalled();
  });

  it("auth handshake with non-active channel token — connects but receives no transcripts", async () => {
    const channel = makeChannel({ active: false });
    mockValidateChannelToken.mockResolvedValue(channel);
    mockGetActive.mockReturnValue(undefined);

    const ws = createMockWs();
    connectChannel(ws);

    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "vx_ch_abc123" })));

    await vi.waitFor(() => {
      const msgs = getSentJSON(ws);
      expect(msgs.find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    // Try sending a transcript — should not be delivered
    const sent = channelServer.sendTranscript("dev1", "hello");
    expect(sent).toBe(false);
  });
});

describe("channel-server transcript routing", () => {
  it("transcript sent to active channel only — other connected channels receive nothing", async () => {
    const activeChannel = makeChannel({ id: "ch-active", active: true });
    const inactiveChannel = makeChannel({ id: "ch-idle", active: false });

    mockGetActive.mockReturnValue(activeChannel);

    // Connect active channel
    const wsActive = createMockWs();
    mockValidateChannelToken.mockResolvedValueOnce(activeChannel);
    connectChannel(wsActive);
    wsActive.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "token-active" })));

    await vi.waitFor(() => {
      expect(getSentJSON(wsActive).find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    // Connect inactive channel
    const wsInactive = createMockWs();
    mockValidateChannelToken.mockResolvedValueOnce(inactiveChannel);
    connectChannel(wsInactive);
    wsInactive.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "token-idle" })));

    await vi.waitFor(() => {
      expect(getSentJSON(wsInactive).find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    // Clear sent messages to isolate transcript test
    wsActive._sent.length = 0;
    wsInactive._sent.length = 0;

    // Send transcript
    const sent = channelServer.sendTranscript("dev1", "What is the weather?");
    expect(sent).toBe(true);

    // Active channel receives transcript
    const activeMessages = getSentJSON(wsActive);
    const transcript = activeMessages.find((m) => m.type === "channel.transcript");
    expect(transcript).toBeTruthy();
    expect(transcript!.deviceId).toBe("dev1");
    expect(transcript!.sessionKey).toBe("vauxr:dev1");
    expect(transcript!.text).toBe("What is the weather?");

    // Inactive channel receives nothing
    expect(getSentJSON(wsInactive)).toHaveLength(0);
  });
});

describe("channel-server response forwarding", () => {
  it("channel.response.delta frames forwarded to correct device", async () => {
    const channel = makeChannel({ id: "ch-1", active: true });
    mockValidateChannelToken.mockResolvedValue(channel);
    mockGetActive.mockReturnValue(channel);

    const ws = createMockWs();
    connectChannel(ws);
    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "vx_ch_abc123" })));

    await vi.waitFor(() => {
      expect(getSentJSON(ws).find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    // Set up response listener
    const deltas: string[] = [];
    channelServer.addResponseListener("dev1", {
      onDelta: (_runId, text) => { deltas.push(text); },
      onEnd: () => {},
      onError: () => {},
    });

    // Simulate delta from channel
    ws.emit("message", Buffer.from(JSON.stringify({
      type: "channel.response.delta",
      deviceId: "dev1",
      runId: "run-1",
      text: "The weather ",
    })));

    expect(deltas).toEqual(["The weather "]);

    channelServer.removeResponseListener("dev1");
  });

  it("channel.response.end signals turn complete", async () => {
    const channel = makeChannel({ id: "ch-1", active: true });
    mockValidateChannelToken.mockResolvedValue(channel);
    mockGetActive.mockReturnValue(channel);

    const ws = createMockWs();
    connectChannel(ws);
    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "vx_ch_abc123" })));

    await vi.waitFor(() => {
      expect(getSentJSON(ws).find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    let ended = false;
    channelServer.addResponseListener("dev1", {
      onDelta: () => {},
      onEnd: () => { ended = true; },
      onError: () => {},
    });

    ws.emit("message", Buffer.from(JSON.stringify({
      type: "channel.response.end",
      deviceId: "dev1",
      runId: "run-1",
    })));

    expect(ended).toBe(true);

    channelServer.removeResponseListener("dev1");
  });

  it("channel.response.error surfaces error to device", async () => {
    const channel = makeChannel({ id: "ch-1", active: true });
    mockValidateChannelToken.mockResolvedValue(channel);
    mockGetActive.mockReturnValue(channel);

    const ws = createMockWs();
    connectChannel(ws);
    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "vx_ch_abc123" })));

    await vi.waitFor(() => {
      expect(getSentJSON(ws).find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    let errorMsg = "";
    channelServer.addResponseListener("dev1", {
      onDelta: () => {},
      onEnd: () => {},
      onError: (_runId, message) => { errorMsg = message; },
    });

    ws.emit("message", Buffer.from(JSON.stringify({
      type: "channel.response.error",
      deviceId: "dev1",
      runId: "run-1",
      message: "Agent error",
    })));

    expect(errorMsg).toBe("Agent error");

    channelServer.removeResponseListener("dev1");
  });
});

describe("channel-server connection drop", () => {
  it("handles connection drop gracefully — next turn falls through correctly", async () => {
    const channel = makeChannel({ id: "ch-1", active: true });
    mockValidateChannelToken.mockResolvedValue(channel);
    mockGetActive.mockReturnValue(channel);

    const ws = createMockWs();
    connectChannel(ws);
    ws.emit("message", Buffer.from(JSON.stringify({ type: "channel.auth", token: "vx_ch_abc123" })));

    await vi.waitFor(() => {
      expect(getSentJSON(ws).find((m) => m.type === "channel.ready")).toBeTruthy();
    });

    // Verify transcript works while connected
    ws._sent.length = 0;
    expect(channelServer.sendTranscript("dev1", "hello")).toBe(true);

    // Simulate disconnect
    ws.close();

    // After disconnect, sendTranscript should return false
    expect(channelServer.sendTranscript("dev1", "hello again")).toBe(false);
  });
});
