import { useCallback, useRef, useState } from "react";

export type ConnectionState =
  | "disconnected"
  | "connected"
  | "listening"
  | "processing"
  | "speaking";

export interface LogEntry {
  ts: number;
  dir: "tx" | "rx" | "sys";
  text: string;
}

interface UseWebSocketOpts {
  onReady: () => void;
  onTranscript: (text: string) => void;
  onAudioStart: (sampleRate: number) => void;
  onAudioFrame: (pcm: ArrayBuffer) => void;
  onAudioEnd: (followUp: boolean) => void;
  onError: (code: string, message: string) => void;
}

export function useWebSocket(opts: UseWebSocketOpts) {
  const wsRef = useRef<WebSocket | null>(null);
  const seqRef = useRef(0);
  const rxAudioFrames = useRef(0);
  const deviceIdRef = useRef("");
  const tokenRef = useRef("");
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [log, setLog] = useState<LogEntry[]>([]);

  const addLog = useCallback((dir: LogEntry["dir"], text: string) => {
    setLog((prev) => [...prev, { ts: Date.now(), dir, text }]);
  }, []);

  const clearLog = useCallback(() => {
    setLog([]);
  }, []);

  const connect = useCallback(
    (url: string, deviceId: string, token: string) => {
      if (wsRef.current) return;
      addLog("sys", `Connecting to ${url}`);
      const ws = new WebSocket(url);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      deviceIdRef.current = deviceId;
      tokenRef.current = token;

      ws.onopen = () => {
        setState("connected");
        addLog("sys", "WebSocket open");
      };

      ws.onmessage = (ev) => {
        if (ev.data instanceof ArrayBuffer) {
          const view = new DataView(ev.data);
          const frameType = view.getUint8(0);
          if (frameType === 0x02 || frameType === 0x03) {
            const payload = ev.data.slice(3);
            rxAudioFrames.current++;
            const label = frameType === 0x03 ? "push" : "tts";
            if (rxAudioFrames.current === 1) {
              addLog("rx", `First ${label} audio frame, ${payload.byteLength} bytes`);
            }
            opts.onAudioFrame(payload);
          }
          return;
        }
        try {
          const msg = JSON.parse(ev.data as string);
          addLog("rx", JSON.stringify(msg));
          switch (msg.type) {
            case "ready":
              setState("connected");
              opts.onReady();
              break;
            case "transcript":
              setState("processing");
              opts.onTranscript(msg.text);
              break;
            case "audio.start":
              if (typeof msg.sample_rate === "number") {
                opts.onAudioStart(msg.sample_rate);
              }
              break;
            case "audio.end": {
              const followUp = msg.follow_up === true;
              addLog("sys", `Playback done, ${rxAudioFrames.current} audio frames received${followUp ? " (follow-up)" : ""}`);
              rxAudioFrames.current = 0;
              setState("connected");
              opts.onAudioEnd(followUp);
              break;
            }
            case "error":
              opts.onError(msg.code, msg.message);
              break;
          }
        } catch {
          addLog("rx", `(unparseable): ${ev.data}`);
        }
      };

      ws.onclose = () => {
        setState("disconnected");
        addLog("sys", "WebSocket closed");
        wsRef.current = null;
      };

      ws.onerror = () => {
        addLog("sys", "WebSocket error");
      };
    },
    [addLog, opts],
  );

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    setState("disconnected");
  }, []);

  const sendVoiceStart = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    seqRef.current = 0;
    const msg = { type: "voice.start", device_id: deviceIdRef.current, token: tokenRef.current };
    ws.send(JSON.stringify(msg));
    addLog("tx", `voice.start device_id=${deviceIdRef.current}`);
  }, [addLog]);

  const sendAudioFrame = useCallback(
    (pcm: Int16Array) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      const seq = seqRef.current++;
      if (seq === 0) addLog("tx", `First audio frame, ${pcm.length} samples`);
      const buf = new ArrayBuffer(3 + pcm.byteLength);
      const view = new DataView(buf);
      view.setUint8(0, 0x01);
      view.setUint16(1, seq & 0xffff, false); // big-endian
      new Uint8Array(buf, 3).set(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength));
      ws.send(buf);
    },
    [],
  );

  const sendJson = useCallback(
    (msg: Record<string, unknown>) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(msg));
      addLog("tx", JSON.stringify(msg));
    },
    [addLog],
  );

  return {
    state,
    setState,
    log,
    addLog,
    clearLog,
    connect,
    disconnect,
    sendVoiceStart,
    sendAudioFrame,
    sendJson,
  };
}
