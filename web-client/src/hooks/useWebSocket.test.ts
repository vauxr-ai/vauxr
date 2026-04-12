import { act, renderHook } from "@testing-library/react";
import type { LogEntry } from "./useWebSocket";
import { useWebSocket } from "./useWebSocket";

// --- Mock WebSocket ---

type WsListener = ((ev: unknown) => void) | null;

class MockWebSocket {
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.OPEN;
  binaryType = "";
  onopen: WsListener = null;
  onmessage: WsListener = null;
  onclose: WsListener = null;
  onerror: WsListener = null;
  sent: unknown[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }

  // Test helpers
  simulateOpen() {
    this.onopen?.({} as Event);
  }

  simulateMessage(data: string | ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }

  simulateClose() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({} as CloseEvent);
  }
}

// Attach static OPEN/CLOSED so code that reads WebSocket.OPEN works
Object.defineProperty(MockWebSocket, "OPEN", { value: 1 });
Object.defineProperty(MockWebSocket, "CLOSED", { value: 3 });

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function defaultOpts() {
  return {
    onReady: vi.fn(),
    onTranscript: vi.fn(),
    onAudioFrame: vi.fn(),
    onAudioEnd: vi.fn(),
    onError: vi.fn(),
  };
}

function lastWs(): MockWebSocket {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1];
}

function findLog(log: LogEntry[], text: string): LogEntry | undefined {
  return log.find((e) => e.text.includes(text));
}

describe("useWebSocket", () => {
  describe("connection", () => {
    it("initial state is disconnected", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      expect(result.current.state).toBe("disconnected");
    });

    it("state becomes connected after ws.onopen fires", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      expect(result.current.state).toBe("connected");
    });

    it("state becomes disconnected after ws.onclose fires", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => lastWs().simulateClose());
      expect(result.current.state).toBe("disconnected");
    });

    it("log entry added on connect with URL", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      expect(findLog(result.current.log, "ws://localhost:8765")).toBeDefined();
    });

    it("log entry added on open", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      expect(findLog(result.current.log, "WebSocket open")).toBeDefined();
    });

    it("log entry added on close", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => lastWs().simulateClose());
      expect(findLog(result.current.log, "WebSocket closed")).toBeDefined();
    });
  });

  describe("binary frames — 0x02 (TTS)", () => {
    function makeBinaryFrame(type: number, payload: Uint8Array): ArrayBuffer {
      const buf = new ArrayBuffer(3 + payload.byteLength);
      const view = new DataView(buf);
      view.setUint8(0, type);
      view.setUint16(1, 0, false);
      new Uint8Array(buf, 3).set(payload);
      return buf;
    }

    it("opts.onAudioFrame called with payload", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const payload = new Uint8Array([1, 2, 3, 4]);
      const frame = makeBinaryFrame(0x02, payload);
      act(() => lastWs().simulateMessage(frame));

      expect(opts.onAudioFrame).toHaveBeenCalledTimes(1);
      const received = new Uint8Array(opts.onAudioFrame.mock.calls[0][0] as ArrayBuffer);
      expect(received).toEqual(payload);
    });

    it("log entry says 'First tts audio frame, N bytes' on first frame", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const payload = new Uint8Array([1, 2, 3, 4]);
      act(() => lastWs().simulateMessage(makeBinaryFrame(0x02, payload)));

      expect(findLog(result.current.log, "First tts audio frame, 4 bytes")).toBeDefined();
    });

    it("no duplicate log entries for subsequent frames", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const payload = new Uint8Array([1, 2]);
      act(() => lastWs().simulateMessage(makeBinaryFrame(0x02, payload)));
      act(() => lastWs().simulateMessage(makeBinaryFrame(0x02, payload)));

      const audioLogs = result.current.log.filter((e) => e.text.includes("First tts audio frame"));
      expect(audioLogs).toHaveLength(1);
    });
  });

  describe("binary frames — 0x03 (push/announce)", () => {
    function makeBinaryFrame(type: number, payload: Uint8Array): ArrayBuffer {
      const buf = new ArrayBuffer(3 + payload.byteLength);
      const view = new DataView(buf);
      view.setUint8(0, type);
      view.setUint16(1, 0, false);
      new Uint8Array(buf, 3).set(payload);
      return buf;
    }

    it("opts.onAudioFrame called with payload", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const payload = new Uint8Array([5, 6, 7]);
      act(() => lastWs().simulateMessage(makeBinaryFrame(0x03, payload)));

      expect(opts.onAudioFrame).toHaveBeenCalledTimes(1);
      const received = new Uint8Array(opts.onAudioFrame.mock.calls[0][0] as ArrayBuffer);
      expect(received).toEqual(payload);
    });

    it("log entry says 'First push audio frame, N bytes' on first frame", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const payload = new Uint8Array([5, 6, 7]);
      act(() => lastWs().simulateMessage(makeBinaryFrame(0x03, payload)));

      expect(findLog(result.current.log, "First push audio frame, 3 bytes")).toBeDefined();
    });

    it("unknown frame types are silently ignored", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const logLenBefore = result.current.log.length;
      act(() => lastWs().simulateMessage(makeBinaryFrame(0xff, new Uint8Array([1]))));

      expect(opts.onAudioFrame).not.toHaveBeenCalled();
      // No new log entries for unknown frame types
      expect(result.current.log.length).toBe(logLenBefore);
    });
  });

  describe("JSON messages", () => {
    it("ready → state becomes connected, opts.onReady called", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => lastWs().simulateMessage(JSON.stringify({ type: "ready" })));

      expect(result.current.state).toBe("connected");
      expect(opts.onReady).toHaveBeenCalledTimes(1);
    });

    it("transcript → state becomes processing, opts.onTranscript called with text", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() =>
        lastWs().simulateMessage(JSON.stringify({ type: "transcript", text: "hello world" })),
      );

      expect(result.current.state).toBe("processing");
      expect(opts.onTranscript).toHaveBeenCalledWith("hello world");
    });

    it("audio.end → state becomes connected, opts.onAudioEnd called, log entry shows frame count", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => lastWs().simulateMessage(JSON.stringify({ type: "audio.end" })));

      expect(result.current.state).toBe("connected");
      expect(opts.onAudioEnd).toHaveBeenCalledTimes(1);
      expect(findLog(result.current.log, "audio frames received")).toBeDefined();
    });

    it("error → opts.onError called with code + message", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() =>
        lastWs().simulateMessage(
          JSON.stringify({ type: "error", code: "AUTH_FAIL", message: "Bad token" }),
        ),
      );

      expect(opts.onError).toHaveBeenCalledWith("AUTH_FAIL", "Bad token");
    });

    it("unparseable message → log entry added with (unparseable):", () => {
      const opts = defaultOpts();
      const { result } = renderHook(() => useWebSocket(opts));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => lastWs().simulateMessage("not json {{{"));

      expect(findLog(result.current.log, "(unparseable):")).toBeDefined();
    });
  });

  describe("sending", () => {
    it("sendVoiceStart sends correct JSON with device_id and token", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => result.current.sendVoiceStart());

      const ws = lastWs();
      expect(ws.sent).toHaveLength(1);
      const msg = JSON.parse(ws.sent[0] as string);
      expect(msg).toEqual({ type: "voice.start", device_id: "dev1", token: "tok" });
    });

    it("sendVoiceStart logs 'voice.start device_id=...'", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());
      act(() => result.current.sendVoiceStart());

      expect(findLog(result.current.log, "voice.start device_id=dev1")).toBeDefined();
    });

    it("sendAudioFrame sends binary frame with type byte 0x01 and correct payload", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const pcm = new Int16Array([100, 200, 300]);
      act(() => result.current.sendAudioFrame(pcm));

      const ws = lastWs();
      expect(ws.sent).toHaveLength(1);
      const buf = ws.sent[0] as ArrayBuffer;
      const view = new DataView(buf);
      expect(view.getUint8(0)).toBe(0x01);
      // Payload starts at offset 3
      const sentPayload = new Int16Array(buf.slice(3));
      expect(sentPayload).toEqual(pcm);
    });

    it("sendAudioFrame logs first frame only", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const pcm = new Int16Array([100]);
      act(() => result.current.sendAudioFrame(pcm));
      act(() => result.current.sendAudioFrame(pcm));

      const audioLogs = result.current.log.filter((e) => e.text.includes("First audio frame"));
      expect(audioLogs).toHaveLength(1);
    });

    it("sendJson sends stringified JSON and logs it", () => {
      const { result } = renderHook(() => useWebSocket(defaultOpts()));
      act(() => result.current.connect("ws://localhost:8765", "dev1", "tok"));
      act(() => lastWs().simulateOpen());

      const msg = { type: "custom", data: 42 };
      act(() => result.current.sendJson(msg));

      const ws = lastWs();
      expect(ws.sent).toHaveLength(1);
      expect(ws.sent[0]).toBe(JSON.stringify(msg));
      expect(findLog(result.current.log, JSON.stringify(msg))).toBeDefined();
    });
  });
});
