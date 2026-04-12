import { describe, it, expect, vi, beforeEach } from "vitest";

// Set env vars before any imports
process.env.OPENCLAW_URL = "wss://test:18789";
process.env.OPENCLAW_TOKEN = "test-token";
process.env.DEVICE_TOKEN = "test-device-token";
process.env.WHISPER_URL = "tcp://127.0.0.1:10300";
process.env.PIPER_URL = "tcp://127.0.0.1:10200";

// Mock wyoming-stt
vi.mock("../src/wyoming-stt.js", () => ({
  transcribe: vi.fn(),
  parseWyomingEvents: vi.fn(),
}));

// Mock wyoming-tts
vi.mock("../src/wyoming-tts.js", () => ({
  synthesize: vi.fn(),
}));

// Mock channel-registry (needed by ChannelServer)
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

import { runVoiceTurn } from "../src/pipeline.js";
import { transcribe } from "../src/wyoming-stt.js";
import { synthesize } from "../src/wyoming-tts.js";
import { OpenClawClient } from "../src/openclaw-client.js";
import { ChannelServer } from "../src/channel-server.js";
import { resetConfig } from "../src/config.js";
import * as channelRegistry from "../src/channel-registry.js";
import type { WebSocket } from "ws";

const mockGetActive = vi.mocked(channelRegistry.getActive);

const mockTranscribe = vi.mocked(transcribe);
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

function createMockOpenClaw(deltas: string[]): OpenClawClient {
  return {
    chat: vi.fn(async (_sessionKey: string, _message: string, onDelta: (text: string) => void) => {
      for (const delta of deltas) {
        onDelta(delta);
      }
    }),
  } as unknown as OpenClawClient;
}

async function* fakeSynthesize(): AsyncGenerator<Buffer> {
  yield Buffer.from("fake-audio-data");
}

function createChannelServer(): ChannelServer {
  return new ChannelServer();
}

describe("pipeline", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetConfig();
    // Default: openclaw-direct active so existing tests route through OpenClawClient
    mockGetActive.mockReturnValue({
      id: "openclaw-direct",
      name: "OpenClaw Direct",
      type: "openclaw-direct",
      tokenHash: "",
      active: true,
      createdAt: new Date(0).toISOString(),
      builtin: true,
    });
  });

  it("runs full voice turn: STT → transcript → LLM → TTS → audio.end", async () => {
    mockTranscribe.mockResolvedValue("what is the weather");
    mockSynthesize.mockImplementation((() => fakeSynthesize()) as unknown as typeof synthesize);

    const ws = createMockWs();
    const oc = createMockOpenClaw([
      "The weather is nice today. ",
      "It will be sunny tomorrow.",
    ]);
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, oc, createChannelServer(), abort.signal);

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string; text?: string });

    expect(jsonMessages.find((m) => m.type === "transcript")).toBeTruthy();
    expect(jsonMessages.find((m) => m.type === "transcript")!.text).toBe("what is the weather");
    expect(jsonMessages.find((m) => m.type === "audio.end")).toBeTruthy();

    // Binary frames should have been sent
    const binaryMessages = ws._sent.filter((m): m is Buffer => typeof m !== "string");
    expect(binaryMessages.length).toBeGreaterThan(0);

    // Each binary frame should have 0x02 type byte
    for (const frame of binaryMessages) {
      expect(frame[0]).toBe(0x02);
    }
  });

  it("sends audio.end even for empty transcript", async () => {
    mockTranscribe.mockResolvedValue("");
    const ws = createMockWs();
    const oc = createMockOpenClaw([]);
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, oc, createChannelServer(), abort.signal);

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string });

    expect(jsonMessages.find((m) => m.type === "audio.end")).toBeTruthy();
    expect(jsonMessages.find((m) => m.type === "transcript")).toBeFalsy();
  });

  it("sends STT_ERROR on transcription failure", async () => {
    mockTranscribe.mockRejectedValue(new Error("whisper down"));
    const ws = createMockWs();
    const oc = createMockOpenClaw([]);
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, oc, createChannelServer(), abort.signal);

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string; code?: string });

    expect(jsonMessages.find((m) => m.type === "error" && m.code === "STT_ERROR")).toBeTruthy();
  });

  it("passes full reply text as a single TTS call", async () => {
    mockTranscribe.mockResolvedValue("tell me a story");

    const ttsCallArgs: string[] = [];
    mockSynthesize.mockImplementation((async function* (text: string) {
      ttsCallArgs.push(text);
      yield Buffer.from("audio");
    }) as unknown as typeof synthesize);

    const oc = {
      chat: vi.fn(async (_sk: string, _msg: string, onDelta: (t: string) => void) => {
        onDelta("Once upon a time there was a cat. ");
        onDelta("Once upon a time there was a cat. The cat sat on a warm cozy mat.");
      }),
    } as unknown as OpenClawClient;

    const ws = createMockWs();
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, oc, createChannelServer(), abort.signal);

    // TTS should have been called exactly once with the full reply
    expect(ttsCallArgs).toEqual([
      "Once upon a time there was a cat. The cat sat on a warm cozy mat.",
    ]);
  });

  it("aborts cleanly when signal is triggered", async () => {
    mockTranscribe.mockResolvedValue("hello world test phrase");
    mockSynthesize.mockImplementation((() => fakeSynthesize()) as unknown as typeof synthesize);

    const abort = new AbortController();
    const ws = createMockWs();
    const oc = {
      chat: vi.fn(async (_sk: string, _msg: string, _onDelta: (t: string) => void) => {
        abort.abort();
      }),
    } as unknown as OpenClawClient;

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, oc, createChannelServer(), abort.signal);

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string });

    // Should NOT have audio.end since we aborted
    expect(jsonMessages.find((m) => m.type === "audio.end")).toBeFalsy();
  });

  // ── Channel routing additions ──

  it("routes transcript to active channel WS when available", async () => {
    const activeChannel = {
      id: "ch-1",
      name: "My Channel",
      type: "openclaw" as const,
      tokenHash: "hash",
      active: true,
      createdAt: new Date().toISOString(),
    };
    mockGetActive.mockReturnValue(activeChannel);
    mockTranscribe.mockResolvedValue("hello from device");
    mockSynthesize.mockImplementation((() => fakeSynthesize()) as unknown as typeof synthesize);

    const ws = createMockWs();
    const channelServer = createChannelServer();

    // Mock sendTranscript to simulate channel accepting transcript + trigger response
    const origSendTranscript = channelServer.sendTranscript.bind(channelServer);
    vi.spyOn(channelServer, "sendTranscript").mockImplementation((deviceId: string, text: string) => {
      // Simulate the channel responding after transcript is sent
      setTimeout(() => {
        const listener = (channelServer as unknown as Record<string, unknown>)["responseListeners"] as Map<string, { onDelta: (runId: string, text: string) => void; onEnd: (runId: string) => void }>;
        const l = listener?.get(deviceId);
        if (l) {
          l.onDelta("run-1", "response text");
          l.onEnd("run-1");
        }
      }, 10);
      return true;
    });

    const abort = new AbortController();
    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, null, channelServer, abort.signal);

    expect(channelServer.sendTranscript).toHaveBeenCalledWith("dev1", "hello from device");
  });

  it("routes to openclaw-direct (OpenClawClient) when openclaw-direct is active and OPENCLAW_URL set", async () => {
    mockGetActive.mockReturnValue({
      id: "openclaw-direct",
      name: "OpenClaw Direct",
      type: "openclaw-direct",
      tokenHash: "",
      active: true,
      createdAt: new Date(0).toISOString(),
      builtin: true,
    });
    mockTranscribe.mockResolvedValue("direct mode test");
    mockSynthesize.mockImplementation((() => fakeSynthesize()) as unknown as typeof synthesize);

    const ws = createMockWs();
    const oc = createMockOpenClaw(["direct reply"]);
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, oc, createChannelServer(), abort.signal);

    expect(oc.chat).toHaveBeenCalled();
  });

  it("drops turn with warning when no active channel and no OPENCLAW_URL", async () => {
    mockGetActive.mockReturnValue(undefined);
    mockTranscribe.mockResolvedValue("nobody is listening");

    const ws = createMockWs();
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, null, createChannelServer(), abort.signal);

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string; code?: string });

    expect(jsonMessages.find((m) => m.type === "error" && m.code === "NO_CHANNEL")).toBeTruthy();
    expect(jsonMessages.find((m) => m.type === "audio.end")).toBeTruthy();
  });

  it("drops turn with warning when active channel has no live WS connection", async () => {
    const activeChannel = {
      id: "ch-1",
      name: "Disconnected Channel",
      type: "openclaw" as const,
      tokenHash: "hash",
      active: true,
      createdAt: new Date().toISOString(),
    };
    mockGetActive.mockReturnValue(activeChannel);
    mockTranscribe.mockResolvedValue("channel is offline");
    mockSynthesize.mockImplementation((() => fakeSynthesize()) as unknown as typeof synthesize);

    const ws = createMockWs();
    const channelServer = createChannelServer();
    // sendTranscript will return false since no WS is connected
    const abort = new AbortController();

    await runVoiceTurn("dev1", [Buffer.alloc(100)], ws, null, channelServer, abort.signal);

    const jsonMessages = ws._sent
      .filter((m): m is string => typeof m === "string")
      .map((m) => JSON.parse(m) as { type: string; code?: string });

    expect(jsonMessages.find((m) => m.type === "error" && m.code === "NO_CHANNEL")).toBeTruthy();
    expect(jsonMessages.find((m) => m.type === "audio.end")).toBeTruthy();
  });
});
