import type { WebSocket } from "ws";
import { transcribe } from "./wyoming-stt.js";
import { synthesize } from "./wyoming-tts.js";
import { OpenClawClient } from "./openclaw-client.js";
import { ChannelServer } from "./channel-server.js";
import { nextSeq } from "./device-registry.js";
import { makeBinaryFrame } from "./utils.js";

function sendJSON(ws: WebSocket, obj: Record<string, unknown>): void {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function sendBinary(ws: WebSocket, deviceId: string, type: number, payload: Buffer): void {
  if (ws.readyState === ws.OPEN) {
    const seq = nextSeq(deviceId);
    ws.send(makeBinaryFrame(type, seq, payload));
  }
}

async function synthesizeAndSend(ws: WebSocket, deviceId: string, text: string, signal: AbortSignal): Promise<void> {
  if (text.length > 0) {
    try {
      for await (const chunk of synthesize(text, signal)) {
        if (signal.aborted) return;
        sendBinary(ws, deviceId, 0x02, chunk);
      }
    } catch (err) {
      console.error("[pipeline] TTS error:", (err as Error).message);
    }
  }
}

async function synthesizeError(ws: WebSocket, deviceId: string, signal: AbortSignal): Promise<void> {
  const errorMsg = "Sorry, I couldn't reach the backend. Please try again later.";
  try {
    for await (const chunk of synthesize(errorMsg, signal)) {
      if (signal.aborted) return;
      sendBinary(ws, deviceId, 0x02, chunk);
    }
  } catch (ttsErr) {
    console.error("[pipeline] TTS error for error message:", (ttsErr as Error).message);
  }
}

async function routeViaOpenClawDirect(
  deviceId: string,
  transcript: string,
  ws: WebSocket,
  openclawClient: OpenClawClient,
  signal: AbortSignal,
): Promise<void> {
  const sessionKey = `vauxr:${deviceId}`;
  let fullReply = "";

  try {
    await openclawClient.chat(sessionKey, transcript, (delta: string) => {
      fullReply = delta;
    });
  } catch (err) {
    sendJSON(ws, { type: "error", code: "BACKEND_ERROR", message: (err as Error).message });
    await synthesizeError(ws, deviceId, signal);
    if (!signal.aborted) sendJSON(ws, { type: "audio.end" });
    return;
  }

  if (signal.aborted) return;

  const replyText = fullReply.trim();
  console.log(`[pipeline] LLM reply (${replyText.length} chars): ${replyText.substring(0, 200)}`);
  await synthesizeAndSend(ws, deviceId, replyText, signal);
  if (!signal.aborted) sendJSON(ws, { type: "audio.end" });
}

const CHANNEL_RESPONSE_TIMEOUT_MS = 60_000;

async function routeViaChannel(
  deviceId: string,
  transcript: string,
  ws: WebSocket,
  channelServer: ChannelServer,
  signal: AbortSignal,
): Promise<void> {
  const sent = channelServer.sendTranscript(deviceId, transcript);
  if (!sent) {
    sendJSON(ws, { type: "error", code: "NO_CHANNEL", message: "Active channel not connected" });
    if (!signal.aborted) sendJSON(ws, { type: "audio.end" });
    return;
  }
  console.log(`[pipeline] Awaiting channel response for ${deviceId}`);

  // Collect deltas from channel until response.end or response.error
  let fullReply: string;
  try {
    fullReply = await new Promise<string>((resolve, reject) => {
      let accumulated = "";
      let resolved = false;

      const timeout = setTimeout(() => {
        if (!resolved) {
          cleanup();
          reject(new Error(`Channel response timeout after ${CHANNEL_RESPONSE_TIMEOUT_MS / 1000}s`));
        }
      }, CHANNEL_RESPONSE_TIMEOUT_MS);

      const cleanup = () => {
        resolved = true;
        clearTimeout(timeout);
        channelServer.removeResponseListener(deviceId);
      };

      channelServer.addResponseListener(deviceId, {
        onDelta: (_runId, text) => {
          if (!resolved) accumulated += text;
        },
        onEnd: (_runId) => {
          if (!resolved) {
            cleanup();
            resolve(accumulated);
          }
        },
        onError: (_runId, message) => {
          if (!resolved) {
            cleanup();
            reject(new Error(message));
          }
        },
      });

      signal.addEventListener("abort", () => {
        if (!resolved) {
          cleanup();
          reject(new Error("Aborted"));
        }
      }, { once: true });
    });
  } catch (err) {
    if (signal.aborted) return;
    sendJSON(ws, { type: "error", code: "BACKEND_ERROR", message: (err as Error).message });
    await synthesizeError(ws, deviceId, signal);
    if (!signal.aborted) sendJSON(ws, { type: "audio.end" });
    return;
  }

  if (signal.aborted) return;

  const replyText = fullReply.trim();
  console.log(`[pipeline] Channel reply (${replyText.length} chars): ${replyText.substring(0, 200)}`);
  await synthesizeAndSend(ws, deviceId, replyText, signal);
  if (!signal.aborted) sendJSON(ws, { type: "audio.end" });
}

export async function runVoiceTurn(
  deviceId: string,
  audioChunks: Buffer[],
  ws: WebSocket,
  openclawClient: OpenClawClient | null,
  channelServer: ChannelServer,
  signal: AbortSignal,
): Promise<void> {
  // Step 1: STT
  if (signal.aborted) return;

  let transcript: string;
  try {
    transcript = await transcribe(audioChunks);
  } catch (err) {
    console.error(`[pipeline] STT error for ${deviceId}:`, (err as Error).message);
    sendJSON(ws, { type: "error", code: "STT_ERROR", message: (err as Error).message });
    return;
  }

  if (signal.aborted) return;

  if (!transcript || transcript.trim().length === 0) {
    console.log(`[pipeline] Empty transcript for ${deviceId} — ending turn`);
    sendJSON(ws, { type: "audio.end" });
    return;
  }

  console.log(`[pipeline] Transcript for ${deviceId}: "${transcript}"`);

  // Step 2: Send transcript to device
  sendJSON(ws, { type: "transcript", text: transcript });

  if (signal.aborted) return;

  // Step 3: Route to active channel
  const active = channelServer.getActiveChannel();

  if (active && active.type === "openclaw-direct" && openclawClient) {
    console.log(`[pipeline] Routing via openclaw-direct for ${deviceId}`);
    await routeViaOpenClawDirect(deviceId, transcript, ws, openclawClient, signal);
  } else if (active && active.type !== "openclaw-direct") {
    console.log(`[pipeline] Routing via channel "${active.name}" (${active.type}) for ${deviceId}`);
    await routeViaChannel(deviceId, transcript, ws, channelServer, signal);
  } else {
    console.warn("[pipeline] No active channel or backend available — dropping turn");
    sendJSON(ws, { type: "error", code: "NO_CHANNEL", message: "No active channel configured" });
    sendJSON(ws, { type: "audio.end" });
  }
}
