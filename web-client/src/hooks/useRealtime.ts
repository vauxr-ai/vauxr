import { useCallback, useEffect, useRef, useState } from "react";

import { observePlayback } from "./realtimeDiagnostics";

type Control = { type: string; code?: string; message?: string; role?: string; text?: string; turn_id?: string; final?: boolean };
type TranscriptTurn = { id: string; role: string; text: string; final: boolean };
export function useRealtime(send: (message: Record<string, unknown>) => void) {
  const [state, setState] = useState<"stopped" | "connecting" | "listening">("stopped");
  const [error, setError] = useState("");
  const [transcript, setTranscript] = useState("");
  const turns = useRef<TranscriptTurn[]>([]);
  const peer = useRef<RTCPeerConnection>();
  const mic = useRef<MediaStream>();
  const output = useRef<HTMLAudioElement>();
  const diagnostics = useRef<ReturnType<typeof observePlayback>>();
  const generation = useRef(0);
  const timer = useRef<number>();
  const readyTimer = useRef<number>();
  const request = useRef<AbortController>();
  const armed = useRef<{ resolve: () => void; reject: (e: Error) => void }>();
  const sendRef = useRef(send); sendRef.current = send;
  const stop = useCallback(() => {
    const wasActive = !!peer.current;
    generation.current++;
    armed.current?.reject(new Error("Realtime stopped")); armed.current = undefined;
    window.clearInterval(timer.current); window.clearTimeout(readyTimer.current);
    request.current?.abort();
    diagnostics.current?.stop(); diagnostics.current = undefined;
    peer.current?.close(); peer.current = undefined;
    mic.current?.getTracks().forEach(t => t.stop()); mic.current = undefined;
    if (output.current) { output.current.pause(); output.current.srcObject = null; }
    output.current = undefined;
    if (wasActive) sendRef.current({ type: "realtime.stop" });
    setState("stopped");
  }, []);
  useEffect(() => stop, [stop]);
  const control = useCallback((message: Control) => {
    if (message.type === "realtime.armed") { armed.current?.resolve(); armed.current = undefined; }
    if (message.type === "realtime.ready" && peer.current) {
      window.clearTimeout(readyTimer.current); setState("listening");
    }
    if (message.type === "realtime.transcript" && peer.current &&
        typeof message.text === "string" && typeof message.turn_id === "string" &&
        (message.role === "user" || message.role === "assistant")) {
      const existing = turns.current.find(turn => turn.id === message.turn_id);
      if (existing?.final) return;
      if (existing) {
        existing.text = message.text.slice(-6000);
        existing.final = message.final === true;
      } else {
        turns.current.push({ id: message.turn_id, role: message.role,
          text: message.text.slice(-6000), final: message.final === true });
      }
      // Bound retained display history as well as the rendered string.
      let length = 0;
      turns.current = turns.current.reverse().filter(turn => {
        const keep = length < 6000;
        length += turn.text.length + 8;
        return keep;
      }).reverse();
      setTranscript(turns.current.map(turn =>
        (turn.role === "user" ? " You: " : " Agent: ") + turn.text).join("").slice(-6000));
    }
    if (message.type === "error" && peer.current) {
      setError(message.message || "Realtime connection failed"); stop();
    }
  }, [stop]);
  const start = useCallback(async (deviceId: string, token: string) => {
    stop(); turns.current = []; setError(""); setTranscript(""); setState("connecting");
    const attempt = generation.current;
    const pc = new RTCPeerConnection(); peer.current = pc;
    const audio = new Audio(); audio.autoplay = true; output.current = audio;
    const observation = observePlayback(pc, audio); diagnostics.current = observation;
    const controller = new AbortController(); request.current = controller;
    readyTimer.current = window.setTimeout(() => { setError("Realtime did not become ready. Check Vauxr's provider and Agent connection."); stop(); }, 45000);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (attempt !== generation.current) { stream.getTracks().forEach(t => t.stop()); return; }
      mic.current = stream;
      stream.getTracks().forEach(t => pc.addTrack(t, stream));
      pc.ontrack = event => {
        if (attempt !== generation.current) return;
        observation.track(event.track);
        audio.srcObject = new MediaStream([event.track]);
        observation.record("play.request");
        void audio.play().then(() => observation.record("play.resolved")).catch(() => {
          if (attempt !== generation.current) return;
          observation.record("play.rejected");
          setError("Audio playback was blocked. Stop and start Realtime again."); stop();
        });
      };
      pc.onconnectionstatechange = () => {
        if (["failed", "disconnected"].includes(pc.connectionState) && attempt === generation.current) {
          setError("Realtime connection lost. Backend actions may continue; check their status before retrying."); stop();
        }
      };
      const data = pc.createDataChannel("chat");
      data.onopen = () => {
        if (attempt !== generation.current) return;
        // Pipecat 1.9 rejects audio writes once the last ping is 3s old.
        // Ping on open, then leave scheduling headroom inside that deadline.
        const ping = () => {
          if (attempt === generation.current && data.readyState === "open") data.send("ping");
        };
        window.clearInterval(timer.current);
        ping();
        timer.current = window.setInterval(ping, 1000);
      };
      const wait = new Promise<void>((resolve, reject) => { armed.current = { resolve, reject }; });
      sendRef.current({ type: "realtime.start", mode: "live" });
      await wait;
      await pc.setLocalDescription(await pc.createOffer());
      if (pc.iceGatheringState !== "complete") await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => { cleanup(); reject(new Error("ICE gathering timed out")); }, 10000);
        const changed = () => { if (pc.iceGatheringState === "complete") { cleanup(); resolve(); } };
        const abort = () => { cleanup(); reject(new Error("Realtime stopped")); };
        function cleanup() { window.clearTimeout(timeout); pc.removeEventListener("icegatheringstatechange", changed); controller.signal.removeEventListener("abort", abort); }
        pc.addEventListener("icegatheringstatechange", changed); controller.signal.addEventListener("abort", abort);
      });
      // The offer is authenticated by the scoped device bearer, never the owner
      // browser session. Omitting cookies keeps the owner CSRF boundary intact.
      const response = await fetch("/api/offer", { method: "POST", signal: controller.signal,
        credentials: "omit", cache: "no-store", redirect: "error", referrerPolicy: "no-referrer",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ sdp: pc.localDescription?.sdp, type: "offer", device_id: deviceId }) });
      if (!response.ok) throw new Error(`Vauxr realtime offer failed (${response.status}). Check Realtime setup and the selected Agent.`);
      const answer = await response.json();
      if (attempt !== generation.current) return;
      await pc.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
    } catch (e) {
      if (attempt !== generation.current) return;
      setError(e instanceof Error ? e.message : "Realtime failed"); stop();
    }
  }, [stop]);
  const volume = useCallback((value: number, muted: boolean) => {
    if (output.current) { output.current.volume = value; output.current.muted = muted; }
  }, []);
  return { state, error, transcript, start, stop, control, volume };
}
