import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import type { Server } from "node:http";

// Set env vars before any imports
process.env.OPENCLAW_URL = "wss://test:18789";
process.env.OPENCLAW_TOKEN = "test-token";
process.env.DEVICE_TOKEN = "test-device-token";
process.env.WHISPER_URL = "tcp://127.0.0.1:10300";
process.env.PIPER_URL = "tcp://127.0.0.1:10200";
process.env.HTTP_PORT = "0"; // random port

// Mock wyoming-tts
vi.mock("../src/wyoming-tts.js", () => ({ synthesize: vi.fn() }));

// Mock device-registry
vi.mock("../src/device-registry.js", () => ({
  getAll: vi.fn(),
  get: vi.fn(),
  setState: vi.fn(),
  nextSeq: vi.fn(() => 0),
  register: vi.fn(),
  unregister: vi.fn(),
  abortActiveTurn: vi.fn(),
}));

// Mock channel-registry
vi.mock("../src/channel-registry.js", () => ({
  getAll: vi.fn(() => []),
  getById: vi.fn(),
  getActive: vi.fn(),
  load: vi.fn(),
  create: vi.fn(),
  remove: vi.fn(),
  activate: vi.fn(),
  rotateToken: vi.fn(),
  validateChannelToken: vi.fn(),
}));

import { startHttpServer } from "../src/http-server.js";
import { resetConfig } from "../src/config.js";
import * as registry from "../src/device-registry.js";
import { synthesize } from "../src/wyoming-tts.js";
import { ChannelServer } from "../src/channel-server.js";
import type { DeviceEntry } from "../src/device-registry.js";
import type { WebSocket } from "ws";

const mockGetAll = vi.mocked(registry.getAll);
const mockGet = vi.mocked(registry.get);
const mockNextSeq = vi.mocked(registry.nextSeq);
const mockSynthesize = vi.mocked(synthesize);

function createMockWs(): WebSocket & { _sent: Array<string | Buffer> } {
  const sent: Array<string | Buffer> = [];
  return {
    OPEN: 1,
    readyState: 1,
    send: vi.fn((data: string | Buffer) => { sent.push(data); }),
    _sent: sent,
  } as unknown as WebSocket & { _sent: Array<string | Buffer> };
}

function makeDevice(overrides: Partial<DeviceEntry> = {}): DeviceEntry {
  const ws = createMockWs();
  return {
    id: "dev1",
    name: "Living Room",
    ws,
    state: "idle",
    lastSeen: new Date("2026-04-06T20:00:00.000Z"),
    seq: 0,
    abortController: null,
    ...overrides,
  };
}

let server: Server;
let baseUrl: string;

beforeAll(async () => {
  resetConfig();
  server = startHttpServer(new ChannelServer());
  await new Promise<void>((resolve) => {
    server.on("listening", resolve);
  });
  const addr = server.address();
  if (typeof addr === "object" && addr) {
    baseUrl = `http://127.0.0.1:${addr.port}`;
  }
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
});

beforeEach(() => {
  vi.clearAllMocks();
});

const AUTH_HEADER = { Authorization: "Bearer test-device-token" };

async function req(
  method: string,
  path: string,
  body?: Record<string, unknown>,
  headers: Record<string, string> = AUTH_HEADER,
): Promise<Response> {
  return fetch(`${baseUrl}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

// ──── Auth ────

describe("Auth", () => {
  it("returns 401 without token", async () => {
    const res = await req("GET", "/api/devices", undefined, {});
    expect(res.status).toBe(401);
  });

  it("returns 401 with wrong token", async () => {
    const res = await req("GET", "/api/devices", undefined, { Authorization: "Bearer wrong" });
    expect(res.status).toBe(401);
  });

  it("returns 200 with correct token", async () => {
    mockGetAll.mockReturnValue([]);
    const res = await req("GET", "/api/devices");
    expect(res.status).toBe(200);
  });
});

// ──── GET /api/devices ────

describe("GET /api/devices", () => {
  it("returns empty array when no devices connected", async () => {
    mockGetAll.mockReturnValue([]);
    const res = await req("GET", "/api/devices");
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual([]);
  });

  it("returns device list with correct shape", async () => {
    const device = makeDevice();
    mockGetAll.mockReturnValue([device]);
    const res = await req("GET", "/api/devices");
    expect(res.status).toBe(200);
    const body = await res.json() as Array<Record<string, unknown>>;
    expect(body).toHaveLength(1);
    expect(body[0]).toEqual({
      id: "dev1",
      name: "Living Room",
      state: "idle",
      lastSeen: "2026-04-06T20:00:00.000Z",
    });
  });

  it("state reflects current device state correctly", async () => {
    const device = makeDevice({ state: "listening" });
    mockGetAll.mockReturnValue([device]);
    const res = await req("GET", "/api/devices");
    const body = await res.json() as Array<Record<string, unknown>>;
    expect(body[0]!.state).toBe("listening");
  });
});

// ──── POST /api/devices/{id}/announce ────

describe("POST /api/devices/{id}/announce", () => {
  it("returns 404 for unknown device ID", async () => {
    mockGet.mockReturnValue(undefined);
    const res = await req("POST", "/api/devices/unknown/announce", { text: "hello" });
    expect(res.status).toBe(404);
  });

  it("returns 409 when device state is listening", async () => {
    mockGet.mockReturnValue(makeDevice({ state: "listening" }));
    const res = await req("POST", "/api/devices/dev1/announce", { text: "hello" });
    expect(res.status).toBe(409);
  });

  it("returns 409 when device state is processing", async () => {
    mockGet.mockReturnValue(makeDevice({ state: "processing" }));
    const res = await req("POST", "/api/devices/dev1/announce", { text: "hello" });
    expect(res.status).toBe(409);
  });

  it("returns 400 when text is missing from body", async () => {
    mockGet.mockReturnValue(makeDevice());
    const res = await req("POST", "/api/devices/dev1/announce", {});
    expect(res.status).toBe(400);
    const body = await res.json() as Record<string, unknown>;
    expect(body.error).toBe("missing text");
  });

  it("returns 400 when body is not valid JSON", async () => {
    mockGet.mockReturnValue(makeDevice());
    const res = await fetch(`${baseUrl}/api/devices/dev1/announce`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...AUTH_HEADER },
      body: "not json{{{",
    });
    expect(res.status).toBe(400);
  });

  it("returns 200 and calls synthesize() with correct text for idle device", async () => {
    const device = makeDevice();
    mockGet.mockReturnValue(device);
    mockNextSeq.mockReturnValue(1);
    mockSynthesize.mockImplementation((async function* () {
      yield Buffer.from("audio-data");
    }) as unknown as typeof synthesize);

    const res = await req("POST", "/api/devices/dev1/announce", { text: "Hello world" });
    expect(res.status).toBe(200);
    expect(mockSynthesize).toHaveBeenCalledWith("Hello world", expect.any(AbortSignal));
  });

  it("audio chunks are sent as 0x03 binary frames to device WS", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    mockNextSeq.mockReturnValue(0);
    mockSynthesize.mockImplementation((async function* () {
      yield Buffer.from("chunk1");
      yield Buffer.from("chunk2");
    }) as unknown as typeof synthesize);

    await req("POST", "/api/devices/dev1/announce", { text: "test" });

    const binaryFrames = ws._sent.filter((m): m is Buffer => typeof m !== "string");
    expect(binaryFrames.length).toBe(2);
    for (const frame of binaryFrames) {
      expect(frame[0]).toBe(0x03);
    }
  });

  it("audio.end JSON frame is sent after all chunks", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    mockNextSeq.mockReturnValue(0);
    mockSynthesize.mockImplementation((async function* () {
      yield Buffer.from("audio");
    }) as unknown as typeof synthesize);

    await req("POST", "/api/devices/dev1/announce", { text: "test" });

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as Record<string, unknown>);

    expect(jsonMessages.find((m) => m.type === "audio.end")).toBeTruthy();
  });
});

// ──── POST /api/devices/{id}/command ────

describe("POST /api/devices/{id}/command", () => {
  it("returns 404 for unknown device ID", async () => {
    mockGet.mockReturnValue(undefined);
    const res = await req("POST", "/api/devices/unknown/command", { command: "mute" });
    expect(res.status).toBe(404);
  });

  it("returns 400 when command is missing", async () => {
    mockGet.mockReturnValue(makeDevice());
    const res = await req("POST", "/api/devices/dev1/command", {});
    expect(res.status).toBe(400);
    const body = await res.json() as Record<string, unknown>;
    expect(body.error).toBe("missing command");
  });

  it("returns 400 for unknown command", async () => {
    mockGet.mockReturnValue(makeDevice());
    const res = await req("POST", "/api/devices/dev1/command", { command: "self_destruct" });
    expect(res.status).toBe(400);
    const body = await res.json() as Record<string, unknown>;
    expect(body.error).toBe("unknown command: self_destruct");
  });

  it("returns 200 and sends device.control frame for set_volume", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    const res = await req("POST", "/api/devices/dev1/command", { command: "set_volume", params: { volume: 75 } });
    expect(res.status).toBe(200);

    const frames = ws._sent.filter((m): m is string => typeof m === "string").map((m) => JSON.parse(m) as Record<string, unknown>);
    expect(frames).toContainEqual({ type: "device.control", command: "set_volume", params: { volume: 75 } });
  });

  it("returns 200 and sends device.control frame for mute", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    const res = await req("POST", "/api/devices/dev1/command", { command: "mute" });
    expect(res.status).toBe(200);

    const frames = ws._sent.filter((m): m is string => typeof m === "string").map((m) => JSON.parse(m) as Record<string, unknown>);
    expect(frames).toContainEqual({ type: "device.control", command: "mute" });
  });

  it("returns 200 and sends device.control frame for unmute", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    const res = await req("POST", "/api/devices/dev1/command", { command: "unmute" });
    expect(res.status).toBe(200);

    const frames = ws._sent.filter((m): m is string => typeof m === "string").map((m) => JSON.parse(m) as Record<string, unknown>);
    expect(frames).toContainEqual({ type: "device.control", command: "unmute" });
  });

  it("returns 200 and sends device.control frame for reboot", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    const res = await req("POST", "/api/devices/dev1/command", { command: "reboot" });
    expect(res.status).toBe(200);

    const frames = ws._sent.filter((m): m is string => typeof m === "string").map((m) => JSON.parse(m) as Record<string, unknown>);
    expect(frames).toContainEqual({ type: "device.control", command: "reboot" });
  });

  it("device.control frame includes params when provided", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    await req("POST", "/api/devices/dev1/command", { command: "set_volume", params: { volume: 50 } });

    const frames = ws._sent.filter((m): m is string => typeof m === "string").map((m) => JSON.parse(m) as Record<string, unknown>);
    const ctrl = frames.find((f) => f.type === "device.control");
    expect(ctrl).toBeTruthy();
    expect(ctrl!.params).toEqual({ volume: 50 });
  });

  it("device.control frame omits params when not provided", async () => {
    const device = makeDevice();
    const ws = device.ws as unknown as WebSocket & { _sent: Array<string | Buffer> };
    mockGet.mockReturnValue(device);
    await req("POST", "/api/devices/dev1/command", { command: "mute" });

    const frames = ws._sent.filter((m): m is string => typeof m === "string").map((m) => JSON.parse(m) as Record<string, unknown>);
    const ctrl = frames.find((f) => f.type === "device.control");
    expect(ctrl).toBeTruthy();
    expect(ctrl).not.toHaveProperty("params");
  });
});

// ──── Routing ────

describe("Routing", () => {
  it("unknown paths return 404", async () => {
    const res = await req("GET", "/api/unknown");
    expect(res.status).toBe(404);
  });

  it("wrong HTTP method returns 405", async () => {
    const res = await req("POST", "/api/devices");
    expect(res.status).toBe(405);
  });
});
