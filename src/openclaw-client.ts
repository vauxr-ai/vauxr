import WebSocket from "ws";
import { v4 as uuid } from "uuid";
import { getConfig } from "./config.js";
import { loadOrCreateIdentity, signConnectPayload, getDeviceToken, saveDeviceToken } from "./device-identity.js";

type ChatState = "delta" | "final" | "error" | "aborted";

interface PendingReq {
  resolve: (payload: Record<string, unknown>) => void;
  reject: (err: Error) => void;
}

interface ChatListener {
  onDelta: (text: string) => void;
  resolve: () => void;
  reject: (err: Error) => void;
}

export class OpenClawClient {
  private ws: WebSocket | null = null;
  private reqId = 0;
  private pendingReqs = new Map<string, PendingReq>();
  private chatListeners = new Map<string, ChatListener>();
  private connected = false;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30_000;
  private shouldReconnect = true;
  private disconnectHandlers: (() => void)[] = [];
  private tickInterval: ReturnType<typeof setInterval> | null = null;

  on(event: "disconnect", handler: () => void): void {
    if (event === "disconnect") this.disconnectHandlers.push(handler);
  }

  async connect(): Promise<void> {
    const config = getConfig();
    const { url, token } = config.openclaw;
    const dataDir = config.dataDir;
    this.shouldReconnect = true;

    // Ensure identity exists
    const identity = loadOrCreateIdentity(dataDir);
    const deviceToken = getDeviceToken(dataDir);

    return new Promise<void>((resolve, reject) => {
      this.ws = new WebSocket(url);

      let handshakeDone = false;

      this.ws.on("open", () => {
        console.log("[openclaw] WebSocket connected, waiting for challenge...");
      });

      this.ws.on("message", (raw: WebSocket.Data) => {
        const msg = JSON.parse(raw.toString()) as {
          type: string;
          id?: number;
          event?: string;
          payload?: Record<string, unknown>;
          method?: string;
        };

        // Challenge → send connect
        if (msg.type === "event" && msg.event === "connect.challenge") {
          const nonce = (msg.payload as Record<string, unknown>)?.nonce as string;
          const id = this.nextReqId();

          this.pendingReqs.set(id, {
            resolve: (payload) => {
              this.connected = true;
              this.reconnectDelay = 1000;
              this.startKeepalive();

              const authInfo = payload.auth as { deviceToken?: string; scopes?: string[] } | undefined;
              console.log("[openclaw] Connected and authenticated");
              if (authInfo?.scopes) {
                console.log("[openclaw] Granted scopes:", authInfo.scopes.join(", "));
              }

              // Store device token if one was issued
              if (authInfo?.deviceToken && authInfo.deviceToken !== deviceToken) {
                saveDeviceToken(dataDir, authInfo.deviceToken);
                console.log("[openclaw] New device token issued and saved");
              }

              if (!handshakeDone) {
                handshakeDone = true;
                resolve();
              }
            },
            reject: (err) => {
              // Check if this is a pairing-required error
              const errMsg = err.message;
              if (errMsg.includes("pairing required") || errMsg.includes("PAIRING_REQUIRED")) {
                console.log("[openclaw] Device not yet paired — approve in OpenClaw UI");
                console.log("[openclaw] Will retry connection until pairing is approved...");
                // Resolve the promise so the server can start up while we retry
                if (!handshakeDone) {
                  handshakeDone = true;
                  resolve();
                }
                return;
              }
              if (!handshakeDone) {
                handshakeDone = true;
                reject(err);
              }
            },
          });

          // Build connect params
          // Always connect as node with device identity.
          // Use deviceToken if paired, otherwise bootstrap with the operator token.
          // Gateway auto-creates a pairing request on first connect.
          const clientId = "gateway-client";
          const clientMode = "backend";
          const role = "operator";
          const scopes = ["operator.read", "operator.write"];
          const authToken = deviceToken || token;

          const { signature, signedAt } = signConnectPayload(dataDir, {
            nonce,
            token: authToken,
            clientId,
            clientMode,
            role,
            scopes,
            platform: "node",
            deviceFamily: "",
          });

          const connectParams: Record<string, unknown> = {
            minProtocol: 3,
            maxProtocol: 3,
            client: {
              id: clientId,
              displayName: "Vauxr",
              version: "0.1.0",
              platform: "node",
              mode: clientMode,
            },
            role,
            scopes,
            caps: ["tool-events"],
            auth: deviceToken
              ? { deviceToken }
              : { token },
            device: {
              id: identity.identity.fingerprint,
              publicKey: identity.identity.publicKeyRaw,
              signature,
              signedAt,
              nonce,
            },
          };

          this.ws!.send(JSON.stringify({
            type: "req",
            id,
            method: "connect",
            params: connectParams,
          }));
          return;
        }

        // Response to a request
        if (msg.type === "res" && msg.id !== undefined) {
          const id = String(msg.id);
          const pending = this.pendingReqs.get(id);
          if (pending) {
            this.pendingReqs.delete(id);
            if ((msg as { ok?: boolean }).ok === false) {
              const errPayload = (msg as { error?: { message?: string } }).error;
              pending.reject(new Error(`OpenClaw error: ${errPayload?.message ?? JSON.stringify(msg)}`));
            } else {
              pending.resolve(msg.payload ?? {});
            }
          }
          return;
        }

        // Chat events
        if (msg.type === "event" && msg.event === "chat") {
          const payload = msg.payload as {
            state: ChatState;
            runId: string;
            message?: { content?: Array<{ text?: string }> };
            errorMessage?: string;
          };
          const listener = this.chatListeners.get(payload.runId);
          if (!listener) return;

          if (payload.state === "delta") {
            const text = payload.message?.content?.[0]?.text ?? "";
            if (text) listener.onDelta(text);
          } else if (payload.state === "final") {
            this.chatListeners.delete(payload.runId);
            listener.resolve();
          } else if (payload.state === "error") {
            this.chatListeners.delete(payload.runId);
            listener.reject(new Error(payload.errorMessage ?? "Chat error"));
          } else if (payload.state === "aborted") {
            this.chatListeners.delete(payload.runId);
            listener.resolve();
          }
          return;
        }

        // Tick — no-op, just keepalive
        if (msg.type === "event" && (msg.event === "tick" || msg.event === "health")) {
          return;
        }
      });

      this.ws.on("close", () => {
        this.connected = false;
        this.stopKeepalive();
        console.log("[openclaw] Disconnected");
        this.disconnectHandlers.forEach((h) => h());

        // Reject all pending
        for (const [, pending] of this.pendingReqs) {
          pending.reject(new Error("OpenClaw disconnected"));
        }
        this.pendingReqs.clear();

        for (const [, listener] of this.chatListeners) {
          listener.reject(new Error("OpenClaw disconnected"));
        }
        this.chatListeners.clear();

        if (this.shouldReconnect) {
          console.log(`[openclaw] Reconnecting in ${this.reconnectDelay}ms...`);
          setTimeout(() => {
            this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
            this.connect().catch((err) => {
              console.error("[openclaw] Reconnect failed:", err);
            });
          }, this.reconnectDelay);
        }
      });

      this.ws.on("error", (err) => {
        console.error("[openclaw] WebSocket error:", err.message);
        if (!handshakeDone) {
          handshakeDone = true;
          reject(err);
        }
      });
    });
  }

  async chat(
    sessionKey: string,
    message: string,
    onDelta: (text: string) => void,
  ): Promise<void> {
    if (!this.ws || !this.connected) {
      throw new Error("OpenClaw not connected");
    }

    const id = this.nextReqId();
    const idempotencyKey = `lv-${Date.now()}-${uuid()}`;

    return new Promise<void>((resolve, reject) => {
      this.pendingReqs.set(id, {
        resolve: (payload) => {
          const runId = (payload as { runId?: string }).runId;
          if (!runId) {
            reject(new Error("No runId in chat.send response"));
            return;
          }
          this.chatListeners.set(runId, { onDelta, resolve, reject });
        },
        reject,
      });

      this.ws!.send(JSON.stringify({
        type: "req",
        id,
        method: "chat.send",
        params: {
          sessionKey,
          message,
          idempotencyKey,
        },
      }));
    });
  }

  close(): void {
    this.shouldReconnect = false;
    this.stopKeepalive();
    this.ws?.close();
  }

  private nextReqId(): string {
    this.reqId = (this.reqId + 1) % 100000;
    return String(this.reqId);
  }

  private startKeepalive(): void {
    this.stopKeepalive();
    this.tickInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.ping();
      }
    }, 30_000);
  }

  private stopKeepalive(): void {
    if (this.tickInterval) {
      clearInterval(this.tickInterval);
      this.tickInterval = null;
    }
  }
}
