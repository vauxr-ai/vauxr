import { WebSocketServer, WebSocket } from "ws";
import { getConfig } from "./config.js";
import { validateToken } from "./auth.js";
import * as registry from "./device-registry.js";
import { OpenClawClient } from "./openclaw-client.js";
import { ChannelServer } from "./channel-server.js";
import * as channelRegistry from "./channel-registry.js";
import { runVoiceTurn } from "./pipeline.js";
import { startHttpServer } from "./http-server.js";

type ConnectionState = "IDLE" | "LISTENING" | "PROCESSING";

interface ConnectionCtx {
  state: ConnectionState;
  deviceId: string | null;
  audioChunks: Buffer[];
}

let openclawClient: OpenClawClient | null = null;
const channelServer = new ChannelServer();

function sendJSON(ws: WebSocket, obj: Record<string, unknown>): void {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function handleTextMessage(ws: WebSocket, ctx: ConnectionCtx, data: string): void {
  let msg: { type: string; device_id?: string; token?: string };
  try {
    msg = JSON.parse(data);
  } catch {
    sendJSON(ws, { type: "error", code: "INVALID_MESSAGE", message: "Invalid JSON" });
    return;
  }

  switch (msg.type) {
    case "voice.start": {
      if (!msg.device_id || !msg.token) {
        sendJSON(ws, { type: "error", code: "INVALID_MESSAGE", message: "Missing device_id or token" });
        return;
      }

      const auth = validateToken(msg.token);
      if (!auth.ok) {
        sendJSON(ws, { type: "error", code: "UNAUTHORIZED", message: auth.reason ?? "Invalid token" });
        ws.close();
        return;
      }

      // Abort any prior turn on this device
      if (ctx.deviceId) {
        registry.abortActiveTurn(ctx.deviceId);
      }

      ctx.deviceId = msg.device_id;
      ctx.audioChunks = [];
      ctx.state = "LISTENING";
      registry.register(msg.device_id, ws, (msg as Record<string, string>).name ?? msg.device_id);
      registry.setState(msg.device_id, "listening");
      sendJSON(ws, { type: "ready" });
      break;
    }

    case "voice.end": {
      if (ctx.state !== "LISTENING" || !ctx.deviceId) {
        sendJSON(ws, { type: "error", code: "INVALID_STATE", message: "Not in listening state" });
        return;
      }

      ctx.state = "PROCESSING";
      registry.setState(ctx.deviceId, "processing");
      const deviceId = ctx.deviceId;
      const chunks = ctx.audioChunks;
      const totalBytes = chunks.reduce((sum, c) => sum + c.length, 0);
      console.log(`[server] voice.end from ${deviceId}: ${chunks.length} chunks, ${totalBytes} bytes`);
      ctx.audioChunks = [];

      const abortController = new AbortController();
      const entry = registry.get(deviceId);
      if (entry) entry.abortController = abortController;

      runVoiceTurn(deviceId, chunks, ws, openclawClient, channelServer, abortController.signal)
        .catch((err) => {
          console.error(`[server] Pipeline error for ${deviceId}:`, err);
          sendJSON(ws, { type: "error", code: "PIPELINE_ERROR", message: (err as Error).message });
        })
        .finally(() => {
          ctx.state = "IDLE";
          registry.setState(deviceId, "idle");
          const e = registry.get(deviceId);
          if (e) e.abortController = null;
        });
      break;
    }

    case "abort": {
      if (ctx.deviceId) {
        registry.abortActiveTurn(ctx.deviceId);
        ctx.state = "IDLE";
        registry.setState(ctx.deviceId, "idle");
        ctx.audioChunks = [];
      }
      break;
    }

    default:
      sendJSON(ws, { type: "error", code: "UNKNOWN_MESSAGE", message: `Unknown type: ${msg.type}` });
  }
}

function handleBinaryMessage(ws: WebSocket, ctx: ConnectionCtx, data: Buffer): void {
  if (data.length < 3) return;

  const msgType = data[0];
  // const seq = data.readUInt16BE(1);  // available for future use
  const payload = data.subarray(3);

  if (msgType === 0x01 && ctx.state === "LISTENING") {
    ctx.audioChunks.push(payload);
    if (ctx.audioChunks.length === 1) {
      console.log(`[server] First audio chunk from ${ctx.deviceId}, ${payload.length} bytes`);
    }
  }
}

async function main(): Promise<void> {
  const config = getConfig();
  console.log(`[server] Starting Vauxr WS server on port ${config.ws.port}...`);

  // Load channel registry
  channelRegistry.load();
  console.log("[server] Channel registry loaded");

  // Only run the embedded direct-operator client when the openclaw-direct
  // channel is the active one. Otherwise the channel-plugin bridge owns
  // the integration and the direct client would just thrash reconnects
  // against an unused endpoint.
  const active = channelRegistry.getActive();
  if (config.openclaw.url && active?.type === "openclaw-direct") {
    openclawClient = new OpenClawClient();
    try {
      await openclawClient.connect();
      console.log("[server] OpenClaw connected (openclaw-direct active channel)");
    } catch (err) {
      console.error("[server] Failed to connect to OpenClaw:", (err as Error).message);
      console.error("[server] Server will start but openclaw-direct channel will fail until OpenClaw reconnects");
    }
  } else if (!config.openclaw.url) {
    console.log("[server] OPENCLAW_URL not set — openclaw-direct channel unavailable");
  }

  const wss = new WebSocketServer({ port: config.ws.port, path: undefined });

  // Attach channel server to handle /channel path
  channelServer.attach(wss);

  wss.on("connection", (ws: WebSocket, req) => {
    const urlPath = req.url ?? "/";
    console.log(`[server] WS connection, req.url=${JSON.stringify(urlPath)}`);
    // Skip channel connections — handled by ChannelServer
    if (urlPath === config.channel.wsPath) return;

    console.log("[server] Device connected");

    const ctx: ConnectionCtx = {
      state: "IDLE",
      deviceId: null,
      audioChunks: [],
    };

    ws.on("message", (raw: Buffer, isBinary: boolean) => {
      if (isBinary) {
        handleBinaryMessage(ws, ctx, raw);
      } else {
        handleTextMessage(ws, ctx, raw.toString("utf-8"));
      }
    });

    ws.on("close", () => {
      console.log(`[server] Device disconnected: ${ctx.deviceId ?? "unknown"}`);
      if (ctx.deviceId) {
        registry.abortActiveTurn(ctx.deviceId);
        registry.unregister(ctx.deviceId);
      }
    });

    ws.on("error", (err) => {
      console.error(`[server] WebSocket error for ${ctx.deviceId ?? "unknown"}:`, err.message);
    });
  });

  console.log(`[server] Vauxr WS server listening on port ${config.ws.port}`);

  startHttpServer(channelServer);
}

main().catch((err) => {
  console.error("[server] Fatal:", err);
  process.exit(1);
});
