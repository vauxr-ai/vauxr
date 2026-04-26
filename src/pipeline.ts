import type { WebSocket } from "ws";
import { transcribe } from "./wyoming-stt.js";
import { synthesize } from "./wyoming-tts.js";
import { OpenClawClient } from "./openclaw-client.js";
import { ChannelServer } from "./channel-server.js";
import { nextSeq, getConfigFor } from "./device-registry.js";
import type { FollowUpMode } from "./device-config.js";
import { makeBinaryFrame } from "./utils.js";
import { getConfig } from "./config.js";
import { IdleSegmenter } from "./idle-segmenter.js";
import { SegmentQueue } from "./segment-queue.js";

const FOLLOW_UP_TAG = "[[follow_up]]";

function sendJSON(ws: WebSocket, obj: Record<string, unknown>): void {
  if (ws.readyState === ws.OPEN) {
    ws.send(JSON.stringify(obj));
  }
}

function sendAudioEnd(ws: WebSocket, followUp: boolean): void {
  sendJSON(ws, { type: "audio.end", follow_up: followUp });
}

function sendBinary(ws: WebSocket, deviceId: string, type: number, payload: Buffer): void {
  if (ws.readyState === ws.OPEN) {
    const seq = nextSeq(deviceId);
    ws.send(makeBinaryFrame(type, seq, payload));
  }
}

interface FollowUpResult {
  followUp: boolean;
  replyText: string;
}

export function resolveFollowUp(fullReply: string, mode: FollowUpMode): FollowUpResult {
  const trimmed = fullReply.trim();

  if (mode === "always") {
    return { followUp: true, replyText: stripFollowUpTag(trimmed) };
  }
  if (mode === "never") {
    return { followUp: false, replyText: stripFollowUpTag(trimmed) };
  }

  // "auto"
  if (trimmed.includes(FOLLOW_UP_TAG)) {
    return { followUp: true, replyText: stripFollowUpTag(trimmed) };
  }
  const trimmedEnd = trimmed.trimEnd();
  if (trimmedEnd.endsWith("?") || trimmedEnd.endsWith("？")) {
    return { followUp: true, replyText: trimmed };
  }
  return { followUp: false, replyText: trimmed };
}

function stripFollowUpTag(text: string): string {
  // Remove the tag plus any surrounding whitespace, then trim ends.
  return text.replace(/\s*\[\[follow_up\]\]\s*/g, " ").trim();
}

// In-stream variant: strip the follow_up tag but keep surrounding
// whitespace so consecutive flushed segments concatenate cleanly.
function stripFollowUpTagInline(text: string): string {
  return text.replace(/\s*\[\[follow_up\]\]\s*/g, " ");
}

async function synthesizeAndSend(ws: WebSocket, deviceId: string, text: string, signal: AbortSignal, targetRate?: number): Promise<void> {
  if (text.length > 0) {
    try {
      let sentStart = false;
      for await (const chunk of synthesize(text, {
        targetRate,
        signal,
        onSampleRate: (rate) => {
          if (!sentStart) {
            sendJSON(ws, { type: "audio.start", sample_rate: rate });
            sentStart = true;
          }
        },
      })) {
        if (signal.aborted) return;
        sendBinary(ws, deviceId, 0x02, chunk);
      }
    } catch (err) {
      console.error("[pipeline] TTS error:", (err as Error).message);
    }
  }
}

async function synthesizeError(ws: WebSocket, deviceId: string, signal: AbortSignal, targetRate?: number): Promise<void> {
  const errorMsg = "Sorry, I couldn't reach the backend. Please try again later.";
  try {
    let sentStart = false;
    for await (const chunk of synthesize(errorMsg, {
      targetRate,
      signal,
      onSampleRate: (rate) => {
        if (!sentStart) {
          sendJSON(ws, { type: "audio.start", sample_rate: rate });
          sentStart = true;
        }
      },
    })) {
      if (signal.aborted) return;
      sendBinary(ws, deviceId, 0x02, chunk);
    }
  } catch (ttsErr) {
    console.error("[pipeline] TTS error for error message:", (ttsErr as Error).message);
  }
}

function getFollowUpMode(deviceId: string): FollowUpMode {
  return getConfigFor(deviceId).follow_up_mode ?? "auto";
}

async function routeViaOpenClawDirect(
  deviceId: string,
  transcript: string,
  ws: WebSocket,
  openclawClient: OpenClawClient,
  signal: AbortSignal,
  targetRate?: number,
): Promise<void> {
  const sessionKey = `vauxr:${deviceId}`;
  let fullReply = "";

  try {
    await openclawClient.chat(sessionKey, transcript, (delta: string) => {
      fullReply = delta;
    });
  } catch (err) {
    sendJSON(ws, { type: "error", code: "BACKEND_ERROR", message: (err as Error).message });
    await synthesizeError(ws, deviceId, signal, targetRate);
    if (!signal.aborted) sendAudioEnd(ws, false);
    return;
  }

  if (signal.aborted) return;

  const { followUp, replyText } = resolveFollowUp(fullReply, getFollowUpMode(deviceId));
  console.log(`[pipeline] LLM reply (${replyText.length} chars, follow_up=${followUp}): ${replyText.substring(0, 200)}`);
  await synthesizeAndSend(ws, deviceId, replyText, signal, targetRate);
  if (!signal.aborted) sendAudioEnd(ws, followUp);
}

const CHANNEL_RESPONSE_TIMEOUT_MS = 60_000;

async function synthesizeSegment(
  ws: WebSocket,
  deviceId: string,
  text: string,
  signal: AbortSignal,
  targetRate: number | undefined,
  state: { sentStart: boolean },
): Promise<void> {
  if (text.length === 0) return;
  for await (const chunk of synthesize(text, {
    targetRate,
    signal,
    onSampleRate: (rate) => {
      if (!state.sentStart) {
        sendJSON(ws, { type: "audio.start", sample_rate: rate });
        state.sentStart = true;
      }
    },
  })) {
    if (signal.aborted) return;
    sendBinary(ws, deviceId, 0x02, chunk);
  }
}

async function routeViaChannel(
  deviceId: string,
  transcript: string,
  ws: WebSocket,
  channelServer: ChannelServer,
  signal: AbortSignal,
  targetRate?: number,
): Promise<void> {
  const sent = channelServer.sendTranscript(deviceId, transcript);
  if (!sent) {
    sendJSON(ws, { type: "error", code: "NO_CHANNEL", message: "Active channel not connected" });
    if (!signal.aborted) sendAudioEnd(ws, false);
    return;
  }
  console.log(`[pipeline] Awaiting channel response for ${deviceId}`);

  const idlePauseMs = getConfig().streamingTts.idlePauseMs;
  const startState = { sentStart: false };

  const queue = new SegmentQueue({
    synthesize: (text) => synthesizeSegment(ws, deviceId, text, signal, targetRate, startState),
    signal,
  });

  // Stream deltas through the idle-segmenter into the synth queue.
  // fullReply is accumulated independently for resolveFollowUp.
  let fullReply: string;
  try {
    fullReply = await new Promise<string>((resolve, reject) => {
      let accumulated = "";
      let resolved = false;

      const segmenter = new IdleSegmenter({
        idlePauseMs,
        onSegment: (segment) => {
          const cleaned = stripFollowUpTagInline(segment);
          if (cleaned.length > 0) queue.push(cleaned);
        },
        onEnd: (finalSegment) => {
          if (finalSegment !== null) {
            const cleaned = stripFollowUpTagInline(finalSegment);
            if (cleaned.length > 0) queue.push(cleaned);
          }
          queue.close();
        },
      });

      const timeout = setTimeout(() => {
        if (!resolved) {
          cleanup();
          segmenter.abort();
          queue.close();
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
          if (resolved) return;
          accumulated += text;
          segmenter.push(text);
        },
        onEnd: (_runId) => {
          if (resolved) return;
          cleanup();
          segmenter.end();
          resolve(accumulated);
        },
        onError: (_runId, message) => {
          if (resolved) return;
          cleanup();
          segmenter.abort();
          queue.close();
          reject(new Error(message));
        },
      });

      signal.addEventListener("abort", () => {
        if (resolved) return;
        cleanup();
        segmenter.abort();
        queue.close();
        reject(new Error("Aborted"));
      }, { once: true });
    });
  } catch (err) {
    await queue.done();
    if (signal.aborted) return;
    sendJSON(ws, { type: "error", code: "BACKEND_ERROR", message: (err as Error).message });
    await synthesizeError(ws, deviceId, signal, targetRate);
    if (!signal.aborted) sendAudioEnd(ws, false);
    return;
  }

  // Wait for the synth worker to drain all flushed segments.
  await queue.done();
  if (signal.aborted) return;

  const { followUp, replyText } = resolveFollowUp(fullReply, getFollowUpMode(deviceId));
  console.log(`[pipeline] Channel reply (${replyText.length} chars, follow_up=${followUp}): ${replyText.substring(0, 200)}`);
  sendAudioEnd(ws, followUp);
}

export async function runVoiceTurn(
  deviceId: string,
  audioChunks: Buffer[],
  ws: WebSocket,
  openclawClient: OpenClawClient | null,
  channelServer: ChannelServer,
  signal: AbortSignal,
  targetRate?: number,
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
    sendAudioEnd(ws, false);
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
    await routeViaOpenClawDirect(deviceId, transcript, ws, openclawClient, signal, targetRate);
  } else if (active && active.type !== "openclaw-direct") {
    console.log(`[pipeline] Routing via channel "${active.name}" (${active.type}) for ${deviceId}`);
    await routeViaChannel(deviceId, transcript, ws, channelServer, signal, targetRate);
  } else {
    console.warn("[pipeline] No active channel or backend available — dropping turn");
    sendJSON(ws, { type: "error", code: "NO_CHANNEL", message: "No active channel configured" });
    sendAudioEnd(ws, false);
  }
}
