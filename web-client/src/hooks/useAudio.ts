import { useCallback, useRef } from "react";

const MIC_SAMPLE_RATE = 16000;
const DEFAULT_PLAYBACK_SAMPLE_RATE = 22050;
const CHUNK_SAMPLES = 1600; // ~100ms at 16kHz

// Served as a static asset from /public so it's delivered as plain JS with
// the correct MIME type. AudioWorklet modules run in an isolated realm where
// Vite's TS/bundler transforms don't apply anyway.
const WORKLET_URL = "/pcm-capture.worklet.js";

interface UseAudioOpts {
  /** Optional callback invoked with each captured PCM chunk (16-bit mono). */
  onPcmChunk?: (pcm: Int16Array) => void;
  /** Optional callback for input level updates (RMS in [0, 1]). Fires per chunk. */
  onInputLevel?: (level: number) => void;
}

export function useAudio(arg: ((pcm: Int16Array) => void) | UseAudioOpts) {
  // Backwards-compatible: callers may pass a bare onPcmChunk function.
  const opts: UseAudioOpts =
    typeof arg === "function" ? { onPcmChunk: arg } : arg;
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);

  // Playback scheduling
  const playCtxRef = useRef<AudioContext | null>(null);
  const playRateRef = useRef(DEFAULT_PLAYBACK_SAMPLE_RATE);
  const nextStartRef = useRef(0);
  const gainNodeRef = useRef<GainNode | null>(null);
  const volumeRef = useRef(1);
  const mutedRef = useRef(false);

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
      let sumSq = 0;
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        sumSq += s * s;
      }
      optsRef.current.onPcmChunk?.(int16);
      if (optsRef.current.onInputLevel) {
        const rms = Math.sqrt(sumSq / float32.length);
        optsRef.current.onInputLevel(rms);
      }
    };

    const source = ctx.createMediaStreamSource(stream);
    source.connect(workletNode);
    // Don't connect worklet to destination — we don't want to hear ourselves
  }, []);

  const stopCapture = useCallback(() => {
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close();
    ctxRef.current = null;
    optsRef.current.onInputLevel?.(0);
  }, []);

  const setPlaybackRate = useCallback((rate: number) => {
    if (rate === playRateRef.current) return;
    playRateRef.current = rate;
    // Close existing context so it gets recreated at the new rate
    if (playCtxRef.current && playCtxRef.current.state !== "closed") {
      playCtxRef.current.close();
      playCtxRef.current = null;
      gainNodeRef.current = null;
    }
  }, []);

  const ensurePlayCtx = useCallback(() => {
    if (!playCtxRef.current || playCtxRef.current.state === "closed") {
      const ctx = new AudioContext({ sampleRate: playRateRef.current });
      const gain = ctx.createGain();
      gain.gain.value = mutedRef.current ? 0 : volumeRef.current;
      gain.connect(ctx.destination);
      playCtxRef.current = ctx;
      gainNodeRef.current = gain;
    }
    return playCtxRef.current;
  }, []);

  const resetPlayback = useCallback(() => {
    nextStartRef.current = 0;
  }, []);

  const stopPlayback = useCallback(() => {
    nextStartRef.current = 0;
    if (playCtxRef.current && playCtxRef.current.state !== "closed") {
      playCtxRef.current.close();
    }
    playCtxRef.current = null;
    gainNodeRef.current = null;
  }, []);

  const setOutputVolume = useCallback((value: number) => {
    const v = Math.max(0, Math.min(1, value));
    volumeRef.current = v;
    if (gainNodeRef.current && !mutedRef.current) {
      gainNodeRef.current.gain.value = v;
    }
  }, []);

  const setMuted = useCallback((muted: boolean) => {
    mutedRef.current = muted;
    if (gainNodeRef.current) {
      gainNodeRef.current.gain.value = muted ? 0 : volumeRef.current;
    }
  }, []);

  const queuePlayback = useCallback(
    (pcmBytes: ArrayBuffer) => {
      const ctx = ensurePlayCtx();
      const gain = gainNodeRef.current!;
      const int16 = new Int16Array(pcmBytes);
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) {
        float32[i] = int16[i] / 32768;
      }

      const buf = ctx.createBuffer(1, float32.length, playRateRef.current);
      buf.copyToChannel(float32, 0);

      const source = ctx.createBufferSource();
      source.buffer = buf;
      source.connect(gain);

      const now = ctx.currentTime;
      const start = Math.max(now, nextStartRef.current);
      source.start(start);
      nextStartRef.current = start + buf.duration;
    },
    [ensurePlayCtx],
  );

  return {
    startCapture,
    stopCapture,
    queuePlayback,
    resetPlayback,
    stopPlayback,
    setPlaybackRate,
    setOutputVolume,
    setMuted,
  };
}
