import { describe, it, expect, vi, beforeAll, afterAll, beforeEach } from "vitest";
import type { Server } from "node:http";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

// Set env vars before any imports
process.env.DEVICE_TOKEN = "test-device-token";
process.env.WHISPER_URL = "tcp://127.0.0.1:10300";
process.env.PIPER_URL = "tcp://127.0.0.1:10200";
process.env.HTTP_PORT = "0"; // random port

// Mock wyoming-tts
vi.mock("../src/wyoming-tts.js", () => ({ synthesize: vi.fn() }));

// Mock device-registry
vi.mock("../src/device-registry.js", () => ({
  getAll: vi.fn(() => []),
  get: vi.fn(),
  setState: vi.fn(),
  nextSeq: vi.fn(() => 0),
  register: vi.fn(),
  unregister: vi.fn(),
  abortActiveTurn: vi.fn(),
}));

import { startHttpServer } from "../src/http-server.js";
import { resetConfig } from "../src/config.js";
import * as channelRegistry from "../src/channel-registry.js";
import { ChannelServer } from "../src/channel-server.js";

let server: Server;
let baseUrl: string;
let dataDir: string;

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

beforeAll(async () => {
  dataDir = mkdtempSync(join(tmpdir(), "vauxr-http-ch-test-"));
  process.env.DATA_DIR = dataDir;
  process.env.OPENCLAW_URL = "wss://test:18789";
  process.env.OPENCLAW_TOKEN = "test-token";
  resetConfig();
  channelRegistry.load();

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
  rmSync(dataDir, { recursive: true, force: true });
});

beforeEach(() => {
  // Reload channels from disk to reset state between tests
  channelRegistry.load();
});

// ── GET /api/channels ──

describe("GET /api/channels", () => {
  it("returns 200 and channel list", async () => {
    const res = await req("GET", "/api/channels");
    expect(res.status).toBe(200);
    const body = await res.json() as Array<Record<string, unknown>>;
    expect(Array.isArray(body)).toBe(true);
    // Should include the openclaw-direct virtual channel since OPENCLAW_URL is set
    expect(body.find((c) => c.id === "openclaw-direct")).toBeTruthy();
  });

  it("returns 401 when no auth token", async () => {
    const res = await req("GET", "/api/channels", undefined, {});
    expect(res.status).toBe(401);
  });

  it("returns 401 with invalid auth token", async () => {
    const res = await req("GET", "/api/channels", undefined, { Authorization: "Bearer wrong" });
    expect(res.status).toBe(401);
  });
});

// ── POST /api/channels ──

describe("POST /api/channels", () => {
  it("returns 201 and creates channel with token shown once", async () => {
    const res = await req("POST", "/api/channels", { name: "My Plugin", type: "openclaw" });
    expect(res.status).toBe(201);
    const body = await res.json() as Record<string, unknown>;
    expect(body.name).toBe("My Plugin");
    expect(body.type).toBe("openclaw");
    expect(body.active).toBe(false);
    expect(body.id).toBeTruthy();
    expect(body.token).toBeTruthy();
    expect(typeof body.token).toBe("string");
    expect((body.token as string)).toMatch(/^vx_ch_[0-9a-f]{64}$/);
    // tokenHash should NOT be in response
    expect(body.tokenHash).toBeUndefined();
  });

  it("returns 400 when name is missing", async () => {
    const res = await req("POST", "/api/channels", { type: "openclaw" });
    expect(res.status).toBe(400);
  });

  it("returns 400 for invalid type", async () => {
    const res = await req("POST", "/api/channels", { name: "Bad", type: "custom" });
    expect(res.status).toBe(400);
  });

  it("returns 401 when no auth token", async () => {
    const res = await req("POST", "/api/channels", { name: "Test", type: "openclaw" }, {});
    expect(res.status).toBe(401);
  });
});

// ── POST /api/channels/:id/activate ──

describe("POST /api/channels/:id/activate", () => {
  it("returns 200 and activates channel", async () => {
    // Create a channel first
    const createRes = await req("POST", "/api/channels", { name: "Activate Me", type: "openclaw" });
    const created = await createRes.json() as Record<string, unknown>;
    const id = created.id as string;

    const res = await req("POST", `/api/channels/${id}/activate`);
    expect(res.status).toBe(200);

    // Verify it's now active
    const listRes = await req("GET", "/api/channels");
    const list = await listRes.json() as Array<Record<string, unknown>>;
    const ch = list.find((c) => c.id === id);
    expect(ch!.active).toBe(true);
  });

  it("returns 404 for non-existent channel", async () => {
    const res = await req("POST", "/api/channels/nonexistent/activate");
    expect(res.status).toBe(404);
  });

  it("returns 401 when no auth token", async () => {
    const res = await req("POST", "/api/channels/some-id/activate", undefined, {});
    expect(res.status).toBe(401);
  });
});

// ── POST /api/channels/:id/rotate ──

describe("POST /api/channels/:id/rotate", () => {
  it("returns 200 and new token", async () => {
    const createRes = await req("POST", "/api/channels", { name: "Rotate Me", type: "openclaw" });
    const created = await createRes.json() as Record<string, unknown>;
    const id = created.id as string;
    const oldToken = created.token as string;

    const res = await req("POST", `/api/channels/${id}/rotate`);
    expect(res.status).toBe(200);
    const body = await res.json() as Record<string, unknown>;
    expect(body.token).toBeTruthy();
    expect(typeof body.token).toBe("string");
    expect((body.token as string)).toMatch(/^vx_ch_[0-9a-f]{64}$/);
    expect(body.token).not.toBe(oldToken);
  });

  it("returns 400 when trying to rotate openclaw-direct builtin", async () => {
    const res = await req("POST", "/api/channels/openclaw-direct/rotate");
    expect(res.status).toBe(400);
    const body = await res.json() as Record<string, unknown>;
    expect(body.error).toContain("built-in");
  });

  it("returns 404 for non-existent channel", async () => {
    const res = await req("POST", "/api/channels/nonexistent/rotate");
    expect(res.status).toBe(404);
  });

  it("returns 401 when no auth token", async () => {
    const res = await req("POST", "/api/channels/some-id/rotate", undefined, {});
    expect(res.status).toBe(401);
  });
});

// ── DELETE /api/channels/:id ──

describe("DELETE /api/channels/:id", () => {
  it("returns 200 and removes channel", async () => {
    const createRes = await req("POST", "/api/channels", { name: "Delete Me", type: "openclaw" });
    const created = await createRes.json() as Record<string, unknown>;
    const id = created.id as string;

    const res = await req("DELETE", `/api/channels/${id}`);
    expect(res.status).toBe(200);

    // Verify it's gone
    const listRes = await req("GET", "/api/channels");
    const list = await listRes.json() as Array<Record<string, unknown>>;
    expect(list.find((c) => c.id === id)).toBeUndefined();
  });

  it("returns 400 when trying to delete openclaw-direct builtin", async () => {
    const res = await req("DELETE", "/api/channels/openclaw-direct");
    expect(res.status).toBe(400);
    const body = await res.json() as Record<string, unknown>;
    expect(body.error).toContain("built-in");
  });

  it("returns 404 for non-existent channel", async () => {
    const res = await req("DELETE", "/api/channels/nonexistent");
    expect(res.status).toBe(404);
  });

  it("returns 401 when no auth token", async () => {
    const res = await req("DELETE", "/api/channels/some-id", undefined, {});
    expect(res.status).toBe(401);
  });
});
