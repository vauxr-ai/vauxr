import { useCallback, useRef } from "react";

const MIC_SAMPLE_RATE = 16000;
const PLAYBACK_SAMPLE_RATE = 22050;
const CHUNK_SAMPLES = 1600; // ~100ms at 16kHz

// Served as a static asset from /public so it's delivered as plain JS with
// the correct MIME type. AudioWorklet modules run in an isolated realm where
// Vite's TS/bundler transforms don't apply anyway.
const WORKLET_URL = "/pcm-capture.worklet.js";

export function useAudio(onPcmChunk: (pcm: Int16Array) => void) {
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);

  // Playback scheduling
  const playCtxRef = useRef<AudioContext | null>(null);
  const nextStartRef = useRef(0);

  const startCapture = useCallback(async () => {
    const ctx = new AudioContext({ sampleRate: MIC_SAMPLE_RATE });
    ctxRef.current = ctx;

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: MIC_SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      },
    });
    streamRef.current = stream;

    await ctx.audioWorklet.addModule(WORKLET_URL);
    const workletNode = new AudioWorkletNode(ctx, "pcm-capture", {
      processorOptions: { chunkSize: CHUNK_SAMPLES },
    });
    workletNodeRef.current = workletNode;

    workletNode.port.onmessage = (ev: MessageEvent) => {
      const float32: Float32Array = ev.data;
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      onPcmChunk(int16);
    };

    const source = ctx.createMediaStreamSource(stream);
    source.connect(workletNode);
    // Don't connect worklet to destination — we don't want to hear ourselves
  }, [onPcmChunk]);

  const stopCapture = useCallback(() => {
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close();
    ctxRef.current = null;
  }, []);

  const ensurePlayCtx = useCallback(() => {
    if (!playCtxRef.current || playCtxRef.current.state === "closed") {
      playCtxRef.current = new AudioContext({ sampleRate: PLAYBACK_SAMPLE_RATE });
    }
    return playCtxRef.current;
  }, []);

  const resetPlayback = useCallback(() => {
    nextStartRef.current = 0;
  }, []);

  const queuePlayback = useCallback(
    (pcmBytes: ArrayBuffer) => {
      const ctx = ensurePlayCtx();
      const int16 = new Int16Array(pcmBytes);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768;
      }

      const buf = ctx.createBuffer(1, float32.length, PLAYBACK_SAMPLE_RATE);
      buf.copyToChannel(float32, 0);

      const source = ctx.createBufferSource();
      source.buffer = buf;
      source.connect(ctx.destination);

      const now = ctx.currentTime;
      const start = Math.max(now, nextStartRef.current);
      source.start(start);
      nextStartRef.current = start + buf.duration;
    },
    [ensurePlayCtx],
  );

  return { startCapture, stopCapture, queuePlayback, resetPlayback };
}
