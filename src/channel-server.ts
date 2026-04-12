import type { WebSocket, WebSocketServer } from "ws";
import type { IncomingMessage } from "node:http";
import * as channelRegistry from "./channel-registry.js";
import type { Channel } from "./channel-registry.js";

interface ChannelConnection {
  ws: WebSocket;
  channel: Channel;
  authenticated: boolean;
}

interface DeviceResponseListener {
  onDelta: (runId: string, text: string) => void;
  onEnd: (runId: string) => void;
  onError: (runId: string, message: string) => void;
}

function sendJSON(ws: WebSocket, obj: Record<string, unknown>): void {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

export class ChannelServer {
  private connections = new Map<string, ChannelConnection>();
  private responseListeners = new Map<string, DeviceResponseListener>();

  attach(wss: WebSocketServer): void {
    wss.on("connection", (ws: WebSocket, req: IncomingMessage) => {
      const urlPath = req.url ?? "/";
      if (urlPath !== "/channel") return;

      this.handleConnection(ws);
    });
  }

  private handleConnection(ws: WebSocket): void {
    console.log("[channel] New channel connection");

    const conn: ChannelConnection = {
      ws,
      channel: null as unknown as Channel,
      authenticated: false,
    };

    // Auth timeout — close if not authenticated within 10s
    const authTimeout = setTimeout(() => {
      if (!conn.authenticated) {
        sendJSON(ws, { type: "error", code: "AUTH_TIMEOUT", message: "Authentication timeout" });
        ws.close();
      }
    }, 10_000);

    ws.on("message", (raw: Buffer) => {
      let msg: { type: string; token?: string; deviceId?: string; runId?: string; text?: string; message?: string };
      try {
        msg = JSON.parse(raw.toString("utf-8"));
        console.log("[channel] received message:", msg.type);
      } catch {
        sendJSON(ws, { type: "error", code: "INVALID_MESSAGE", message: "Invalid JSON" });
        return;
      }

      if (!conn.authenticated) {
        if (msg.type === "channel.auth") {
          void this.handleAuth(ws, conn, msg.token ?? "", authTimeout);
        } else {
          sendJSON(ws, { type: "error", code: "UNAUTHORIZED", message: "Must authenticate first" });
        }
        return;
      }

      const deviceId = msg.deviceId;
      const runId = msg.runId;
      if (!deviceId || !runId) {
        console.warn(
          `[channel] ${conn.channel.name}: ignoring ${msg.type} — missing deviceId or runId`,
        );
        return;
      }

      const listener = this.responseListeners.get(deviceId);
      if (!listener) {
        console.warn(
          `[channel] ${conn.channel.name}: no listener for ${deviceId} (${msg.type})`,
        );
        return;
      }

      switch (msg.type) {
        case "channel.response.delta":
          if (msg.text !== undefined) {
            listener.onDelta(runId, msg.text);
          }
          break;
        case "channel.response.end":
          listener.onEnd(runId);
          break;
        case "channel.response.error":
          listener.onError(runId, msg.message ?? "Channel error");
          break;
        default:
          sendJSON(ws, { type: "error", code: "UNKNOWN_MESSAGE", message: `Unknown type: ${msg.type}` });
      }
    });

    ws.on("close", () => {
      clearTimeout(authTimeout);
      if (conn.authenticated) {
        console.log(`[channel] Channel disconnected: ${conn.channel.name} (${conn.channel.id})`);
        // Only remove from registry if this conn is still the active one.
        // Otherwise we'd wipe out a replacement connection that already took
        // this channel's slot (see handleAuth: when a second conn authenticates
        // with the same token, it kicks the previous one — the kicked conn's
        // close fires AFTER the replacement is stored, so an unconditional
        // delete here would clear the live entry).
        if (this.connections.get(conn.channel.id) === conn) {
          this.connections.delete(conn.channel.id);
        }
      } else {
        console.log("[channel] Unauthenticated channel connection closed");
      }
    });

    ws.on("error", (err) => {
      console.error(`[channel] WebSocket error:`, err.message);
    });
  }

  private async handleAuth(ws: WebSocket, conn: ChannelConnection, token: string, authTimeout: ReturnType<typeof setTimeout>): Promise<void> {
    const channel = await channelRegistry.validateChannelToken(token);
    if (!channel) {
      sendJSON(ws, { type: "error", code: "UNAUTHORIZED", message: "Invalid channel token" });
      ws.close();
      clearTimeout(authTimeout);
      return;
    }

    clearTimeout(authTimeout);
    conn.authenticated = true;
    conn.channel = channel;

    // Close any existing connection for this channel
    const existing = this.connections.get(channel.id);
    if (existing) {
      existing.ws.close();
    }

    this.connections.set(channel.id, conn);
    sendJSON(ws, { type: "channel.ready", channelId: channel.id, name: channel.name });
    console.log(`[channel] Channel authenticated: ${channel.name} (${channel.id})`);
  }

  sendTranscript(deviceId: string, text: string): boolean {
    const active = channelRegistry.getActive();
    if (!active) {
      console.warn("[channel] No active channel — dropping transcript");
      return false;
    }

    if (active.type === "openclaw-direct") {
      return false;
    }

    const conn = this.connections.get(active.id);
    if (!conn || conn.ws.readyState !== conn.ws.OPEN) {
      console.warn(`[channel] Active channel ${active.name} not connected — dropping transcript`);
      return false;
    }

    sendJSON(conn.ws, {
      type: "channel.transcript",
      deviceId,
      sessionKey: `vauxr:${deviceId}`,
      text,
    });
    console.log(`[channel] Sent transcript to ${active.name}: "${text}"`);
    return true;
  }

  addResponseListener(deviceId: string, listener: DeviceResponseListener): void {
    this.responseListeners.set(deviceId, listener);
  }

  removeResponseListener(deviceId: string): void {
    this.responseListeners.delete(deviceId);
  }

  getActiveChannel(): Channel | undefined {
    return channelRegistry.getActive();
  }

  isActiveConnected(): boolean {
    const active = channelRegistry.getActive();
    if (!active) return false;
    if (active.type === "openclaw-direct") return true;
    const conn = this.connections.get(active.id);
    return !!conn && conn.ws.readyState === conn.ws.OPEN;
  }
}
