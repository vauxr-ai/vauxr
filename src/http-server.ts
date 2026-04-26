import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { join, extname } from "node:path";
import { synthesize } from "./wyoming-tts.js";
import * as registry from "./device-registry.js";
import * as channelRegistry from "./channel-registry.js";
import { makeBinaryFrame } from "./utils.js";
import { getConfig } from "./config.js";
import { validateChannelHttpToken, validateToken } from "./auth.js";
import type { ChannelServer } from "./channel-server.js";
import type { DeviceConfig, FollowUpMode } from "./device-config.js";

const VALID_COMMANDS = new Set(["set_volume", "mute", "unmute", "reboot"]);

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
};

function parseBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      if (!raw) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(raw));
      } catch {
        reject(new Error("invalid JSON"));
      }
    });
    req.on("error", reject);
  });
}

function sendJSON(res: ServerResponse, status: number, body: Record<string, unknown> | Record<string, unknown>[]): void {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

async function validateAuth(req: IncomingMessage): Promise<boolean> {
  const header = req.headers.authorization;
  if (!header || !header.startsWith("Bearer ")) return false;
  const token = header.slice(7);
  if (validateToken(token).ok) return true;
  return (await validateChannelHttpToken(token)).ok;
}

function parseRoute(url: string): { path: string; segments: string[] } {
  const path = url.split("?")[0] ?? "";
  const segments = path.split("/").filter(Boolean);
  return { path, segments };
}

async function handleDevices(_req: IncomingMessage, res: ServerResponse): Promise<void> {
  const devices = registry.getAll().map((d) => ({
    id: d.id,
    name: d.name,
    state: d.state,
    lastSeen: d.lastSeen.toISOString(),
    config: d.config,
  }));
  console.log(`[http] 200 devices listed: ${devices.length}`);
  sendJSON(res, 200, devices);
}

const VALID_FOLLOW_UP_MODES: ReadonlySet<FollowUpMode> = new Set(["auto", "always", "never"]);

async function handleUpdateDevice(req: IncomingMessage, res: ServerResponse, deviceId: string): Promise<void> {
  const device = registry.get(deviceId);
  if (!device) {
    console.log(`[http] 404 device not found: ${deviceId}`);
    sendJSON(res, 404, { error: "device not found" });
    return;
  }

  let body: Record<string, unknown>;
  try {
    body = await parseBody(req) as Record<string, unknown>;
  } catch {
    console.log(`[http] 400 invalid JSON`);
    sendJSON(res, 400, { error: "invalid JSON" });
    return;
  }

  const patch: DeviceConfig = {};
  if (body.name !== undefined) {
    if (typeof body.name !== "string") {
      sendJSON(res, 400, { error: "name must be a string" });
      return;
    }
    patch.name = body.name;
  }
  if (body.voice !== undefined) {
    if (typeof body.voice !== "boolean") {
      sendJSON(res, 400, { error: "voice must be a boolean" });
      return;
    }
    patch.voice = body.voice;
  }
  if (body.follow_up_mode !== undefined) {
    if (typeof body.follow_up_mode !== "string" || !VALID_FOLLOW_UP_MODES.has(body.follow_up_mode as FollowUpMode)) {
      sendJSON(res, 400, { error: "follow_up_mode must be 'auto' | 'always' | 'never'" });
      return;
    }
    patch.follow_up_mode = body.follow_up_mode as FollowUpMode;
  }

  const next = registry.updateConfig(deviceId, patch);
  if (next.name) {
    device.name = next.name;
  }
  console.log(`[http] 200 device updated: ${deviceId}`);
  sendJSON(res, 200, {
    id: device.id,
    name: device.name,
    state: device.state,
    lastSeen: device.lastSeen.toISOString(),
    config: next,
  });
}

async function handleAnnounce(req: IncomingMessage, res: ServerResponse, deviceId: string): Promise<void> {
  const device = registry.get(deviceId);
  if (!device) {
    console.log(`[http] 404 device not found: ${deviceId}`);
    sendJSON(res, 404, { error: "device not found" });
    return;
  }

  if (device.state === "listening" || device.state === "processing") {
    console.log(`[http] 409 device busy: ${deviceId}`);
    sendJSON(res, 409, { error: "device busy" });
    return;
  }

  let body: Record<string, unknown>;
  try {
    body = await parseBody(req) as Record<string, unknown>;
  } catch {
    console.log(`[http] 400 invalid JSON`);
    sendJSON(res, 400, { error: "invalid JSON" });
    return;
  }

  if (!body.text || typeof body.text !== "string") {
    console.log(`[http] 400 missing text`);
    sendJSON(res, 400, { error: "missing text" });
    return;
  }

  const text = body.text;
  console.log(`[http] announce: synthesizing for ${deviceId} "${text}"`);

  const abortController = new AbortController();
  let chunkCount = 0;
  try {
    let sentStart = false;
    for await (const chunk of synthesize(text, {
      targetRate: device.outputSampleRate,
      signal: abortController.signal,
      onSampleRate: (rate) => {
        if (!sentStart && device.ws.readyState === device.ws.OPEN) {
          device.ws.send(JSON.stringify({ type: "audio.start", sample_rate: rate }));
          sentStart = true;
        }
      },
    })) {
      const seq = registry.nextSeq(deviceId);
      const frame = makeBinaryFrame(0x03, seq, chunk);
      device.ws.send(frame);
      chunkCount++;
    }
  } catch (err) {
    console.error(`[http] TTS error for announce to ${deviceId}:`, (err as Error).message);
  }

  if (device.ws.readyState === device.ws.OPEN) {
    device.ws.send(JSON.stringify({ type: "audio.end" }));
  }

  console.log(`[http] announce: done ${deviceId}, ${chunkCount} chunks sent`);
  console.log(`[http] 200 announce → ${deviceId}`);
  sendJSON(res, 200, { ok: true });
}

async function handleCommand(req: IncomingMessage, res: ServerResponse, deviceId: string): Promise<void> {
  const device = registry.get(deviceId);
  if (!device) {
    console.log(`[http] 404 device not found: ${deviceId}`);
    sendJSON(res, 404, { error: "device not found" });
    return;
  }

  let body: Record<string, unknown>;
  try {
    body = await parseBody(req) as Record<string, unknown>;
  } catch {
    console.log(`[http] 400 invalid JSON`);
    sendJSON(res, 400, { error: "invalid JSON" });
    return;
  }

  if (!body.command || typeof body.command !== "string") {
    console.log(`[http] 400 missing command`);
    sendJSON(res, 400, { error: "missing command" });
    return;
  }

  if (!VALID_COMMANDS.has(body.command)) {
    console.log(`[http] 400 unknown command: ${body.command}`);
    sendJSON(res, 400, { error: `unknown command: ${body.command}` });
    return;
  }

  const frame: Record<string, unknown> = {
    type: "device.control",
    command: body.command,
  };
  if (body.params !== undefined) {
    frame.params = body.params;
  }

  if (device.ws.readyState === device.ws.OPEN) {
    device.ws.send(JSON.stringify(frame));
  }

  console.log(`[http] command: ${body.command} → ${deviceId}`);
  console.log(`[http] 200 command → ${deviceId}`);
  sendJSON(res, 200, { ok: true });
}

// --- Channel CRUD handlers ---

async function handleListChannels(_req: IncomingMessage, res: ServerResponse): Promise<void> {
  const channels = channelRegistry.getAll();
  console.log(`[http] 200 channels listed: ${channels.length}`);
  sendJSON(res, 200, channels as unknown as Record<string, unknown>[]);
}

async function handleCreateChannel(req: IncomingMessage, res: ServerResponse): Promise<void> {
  let body: Record<string, unknown>;
  try {
    body = await parseBody(req) as Record<string, unknown>;
  } catch {
    sendJSON(res, 400, { error: "invalid JSON" });
    return;
  }

  if (!body.name || typeof body.name !== "string") {
    sendJSON(res, 400, { error: "missing name" });
    return;
  }

  const type = (body.type as string) || "openclaw";
  if (type !== "openclaw") {
    sendJSON(res, 400, { error: "invalid type, must be 'openclaw'" });
    return;
  }

  const { channel, token } = await channelRegistry.create(body.name, type);
  console.log(`[http] 201 channel created: ${channel.name} (${channel.id})`);
  sendJSON(res, 201, { ...channel, token } as unknown as Record<string, unknown>);
}

async function handleDeleteChannel(_req: IncomingMessage, res: ServerResponse, id: string): Promise<void> {
  const channel = channelRegistry.getById(id);
  if (!channel) {
    sendJSON(res, 404, { error: "channel not found" });
    return;
  }
  if (channel.builtin) {
    sendJSON(res, 400, { error: "cannot delete built-in channel" });
    return;
  }
  channelRegistry.remove(id);
  console.log(`[http] 200 channel deleted: ${id}`);
  sendJSON(res, 200, { ok: true });
}

async function handleActivateChannel(_req: IncomingMessage, res: ServerResponse, id: string): Promise<void> {
  const ok = channelRegistry.activate(id);
  if (!ok) {
    sendJSON(res, 404, { error: "channel not found" });
    return;
  }
  console.log(`[http] 200 channel activated: ${id}`);
  sendJSON(res, 200, { ok: true });
}

async function handleRotateToken(_req: IncomingMessage, res: ServerResponse, id: string): Promise<void> {
  const channel = channelRegistry.getById(id);
  if (!channel) {
    sendJSON(res, 404, { error: "channel not found" });
    return;
  }
  if (channel.builtin) {
    sendJSON(res, 400, { error: "cannot rotate token for built-in channel" });
    return;
  }
  const token = await channelRegistry.rotateToken(id);
  if (!token) {
    sendJSON(res, 404, { error: "channel not found" });
    return;
  }
  console.log(`[http] 200 token rotated: ${id}`);
  sendJSON(res, 200, { token });
}

// --- Static file serving ---

function serveStatic(req: IncomingMessage, res: ServerResponse, urlPath: string): void {
  const distDir = join(process.cwd(), "web-client", "dist");
  if (!existsSync(distDir)) {
    sendJSON(res, 404, { error: "not found" });
    return;
  }

  // Map URL to file path
  let filePath = join(distDir, urlPath);

  // SPA fallback: if the file doesn't exist or is a directory, serve index.html
  if (!existsSync(filePath) || statSync(filePath).isDirectory()) {
    filePath = join(distDir, "index.html");
  }

  if (!existsSync(filePath)) {
    sendJSON(res, 404, { error: "not found" });
    return;
  }

  const ext = extname(filePath);
  const contentType = MIME_TYPES[ext] || "application/octet-stream";
  res.writeHead(200, { "Content-Type": contentType });
  createReadStream(filePath).pipe(res);
}

export function startHttpServer(_channelServer: ChannelServer): ReturnType<typeof createServer> {
  const config = getConfig();
  const port = config.http.port;

  const server = createServer((req: IncomingMessage, res: ServerResponse) => {
    void (async () => {
      const method = req.method ?? "GET";
      const { path, segments } = parseRoute(req.url ?? "/");

      console.log(`[http] ${method} ${path}`);

      // CORS headers on every response
      res.setHeader("Access-Control-Allow-Origin", "*");
      res.setHeader("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS");
      res.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type");
      res.setHeader("Access-Control-Max-Age", "86400");

      // CORS preflight — answer before auth (browsers don't send Authorization on preflight)
      if (method === "OPTIONS") {
        res.writeHead(204);
        res.end();
        return;
      }

      // Static files — no auth required for portal
      if (segments[0] !== "api") {
        serveStatic(req, res, path);
        return;
      }

      // Auth check for API routes
      if (!await validateAuth(req)) {
        console.log(`[http] 401 unauthorized`);
        sendJSON(res, 401, { error: "unauthorized" });
        return;
      }

      // GET /api/devices
      if (segments[0] === "api" && segments[1] === "devices" && segments.length === 2) {
        if (method !== "GET") {
          sendJSON(res, 405, { error: "method not allowed" });
          return;
        }
        await handleDevices(req, res);
        return;
      }

      // PATCH /api/devices/{id}
      if (segments[0] === "api" && segments[1] === "devices" && segments.length === 3) {
        if (method !== "PATCH") {
          sendJSON(res, 405, { error: "method not allowed" });
          return;
        }
        await handleUpdateDevice(req, res, segments[2]!);
        return;
      }

      // POST /api/devices/{id}/announce
      if (segments[0] === "api" && segments[1] === "devices" && segments[3] === "announce" && segments.length === 4) {
        if (method !== "POST") {
          sendJSON(res, 405, { error: "method not allowed" });
          return;
        }
        await handleAnnounce(req, res, segments[2]!);
        return;
      }

      // POST /api/devices/{id}/command
      if (segments[0] === "api" && segments[1] === "devices" && segments[3] === "command" && segments.length === 4) {
        if (method !== "POST") {
          sendJSON(res, 405, { error: "method not allowed" });
          return;
        }
        await handleCommand(req, res, segments[2]!);
        return;
      }

      // --- Channel routes ---

      // GET /api/channels
      if (segments[0] === "api" && segments[1] === "channels" && segments.length === 2) {
        if (method === "GET") {
          await handleListChannels(req, res);
          return;
        }
        if (method === "POST") {
          await handleCreateChannel(req, res);
          return;
        }
        sendJSON(res, 405, { error: "method not allowed" });
        return;
      }

      // DELETE /api/channels/:id
      if (segments[0] === "api" && segments[1] === "channels" && segments.length === 3) {
        if (method === "DELETE") {
          await handleDeleteChannel(req, res, segments[2]!);
          return;
        }
        sendJSON(res, 405, { error: "method not allowed" });
        return;
      }

      // POST /api/channels/:id/activate
      if (segments[0] === "api" && segments[1] === "channels" && segments[3] === "activate" && segments.length === 4) {
        if (method !== "POST") {
          sendJSON(res, 405, { error: "method not allowed" });
          return;
        }
        await handleActivateChannel(req, res, segments[2]!);
        return;
      }

      // POST /api/channels/:id/rotate
      if (segments[0] === "api" && segments[1] === "channels" && segments[3] === "rotate" && segments.length === 4) {
        if (method !== "POST") {
          sendJSON(res, 405, { error: "method not allowed" });
          return;
        }
        await handleRotateToken(req, res, segments[2]!);
        return;
      }

      console.log(`[http] 404 not found`);
      sendJSON(res, 404, { error: "not found" });
    })();
  });

  server.listen(port, () => {
    console.log(`[http] HTTP API server listening on port ${port}`);
  });

  return server;
}
