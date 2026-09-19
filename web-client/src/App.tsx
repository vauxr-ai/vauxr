import { ABORT_MESSAGE } from "./hooks/voiceProtocol";
import { ownerFetch } from "./auth/api";
import { SpeechSegmenter } from "./hooks/speechSegmenter";
import { useRealtime } from "./hooks/useRealtime";
import OwnerGate from "./auth/OwnerGate";
import AccessPanel from "./components/AccessPanel";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Layout from "./components/Layout";
import Sidebar, { type SectionId } from "./components/Sidebar";
import TalkPanel, { type TalkMode } from "./components/TalkPanel";
import ResizableSplit from "./components/ResizableSplit";
import StatusBar from "./components/StatusBar";
import EventLog from "./components/EventLog";
import ConfigPanel from "./components/ConfigPanel";
import AgentsPanel from "./components/AgentsPanel";
import DevicesPanel from "./components/DevicesPanel";
import SettingsPanel from "./components/SettingsPanel";
import { useWebSocket } from "./hooks/useWebSocket";
import { useAudio } from "./hooks/useAudio";

const CONNECTED_STATES = [
  "connected",
  "listening",
  "processing",
  "speaking",
] as const;

export default function App() {
  return (
    <OwnerGate>
      <OwnerApp />
    </OwnerGate>
  );
}

function OwnerApp() {
  const [connectionEpoch, setConnectionEpoch] = useState(0);
  const [modeLoaded, setModeLoaded] = useState(false);
  const [modeBusy, setModeBusy] = useState(false);
  const [modeError, setModeError] = useState("");
  const modeRequest = useRef(0);
  const segmenter = useRef(new SpeechSegmenter());
  const acceptPlayback = useRef(false);
  const awaitingReady = useRef(false);
  const responseExpected = useRef(false);
  const credential = useRef("");
  const [transcript, setTranscript] = useState("");
  const [talking, setTalking] = useState(false);
  const [followUpListening, setFollowUpListening] = useState(false);
  const talkingRef = useRef(false);
  const captureReadyRef = useRef(false);
  const captureGeneration = useRef(0);
  const [wsUrl, setWsUrl] = useState("");

  const [deviceId, setDeviceId] = useState("");

  const [activeSection, setActiveSection] = useState<SectionId>("connection");
  const [talkMode, setTalkMode] = useState<TalkMode>("hold");
  const talkModeRef = useRef(talkMode);
  talkModeRef.current = talkMode;
  const [inputLevel, setInputLevel] = useState(0);
  const [outputVolume, setOutputVolumeState] = useState(0.85);
  const [outputMuted, setOutputMutedState] = useState(false);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  // Round-trip latency: voice.end → first audio frame back from server.
  const pendingLatencyStart = useRef<number | null>(null);

  const wsOpts = useMemo(
    () => ({
      onReady: () => {},
      onTranscript: (text: string) => setTranscript(text),
      onAudioStart: (sampleRate: number) => {
        audio.setPlaybackRate(sampleRate);
      },
      onAudioFrame: (pcm: ArrayBuffer) => {
        if (pendingLatencyStart.current != null) {
          setLatencyMs(
            Math.round(performance.now() - pendingLatencyStart.current),
          );
          pendingLatencyStart.current = null;
        }
        ws.setState("speaking");
        audio.queuePlayback(pcm);
      },
      onAudioEnd: (followUp: boolean) => {
        audio.resetPlayback();
        setFollowUpListening(followUp);
      },
      onError: (_code: string, _message: string) => {},
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const ws = useWebSocket(wsOpts);
  const realtime = useRealtime(ws.sendJson);
  Object.assign(wsOpts, { onControl: realtime.control });
  const audio = useAudio({
    onPcmChunk: useCallback(
      (pcm: Int16Array) => {
        if (!talkingRef.current || !captureReadyRef.current) return;
        if (talkMode === "toggle") {
          segmenter.current.push(pcm, () => {
            acceptPlayback.current = false;
            audio.stopPlayback();
            ws.sendJson(ABORT_MESSAGE);
            awaitingReady.current = true;
            responseExpected.current = false;
            ws.sendVoiceStart();
            ws.setState("listening");
          }, ws.sendAudioFrame, () => {
            responseExpected.current = true;
            ws.sendJson({ type: "voice.end" });
            ws.setState("processing");
            pendingLatencyStart.current = performance.now();
          });
        } else ws.sendAudioFrame(pcm);
      },
      [ws, talkMode],
    ),
    onInputLevel: useCallback((level: number) => {
      setInputLevel(level);
    }, []),
  });

  useEffect(() => {
    const stop = () => {
      responseExpected.current = false;
      segmenter.current.reset();
      acceptPlayback.current = false;
      captureGeneration.current++;
      captureReadyRef.current = false;
      talkingRef.current = false;
      setTalking(false);
      audio.stopCapture();
      audio.stopPlayback();
      realtime.stop();
      ws.disconnect();
    };
    window.addEventListener("voice-stop", stop);
    return () => {
      window.removeEventListener("voice-stop", stop);
      stop();
    };
  }, []);
  useEffect(() => {
    if (ws.state === "disconnected") {
      realtime.stop();
      responseExpected.current = false;
      segmenter.current.reset();
      acceptPlayback.current = false;
      captureGeneration.current++;
      captureReadyRef.current = false;
      talkingRef.current = false;
      setTalking(false);
      audio.stopCapture();
      audio.stopPlayback();
    }
  }, [ws.state]);

  // Patch the memoized opts to use live refs
  wsOpts.onReady = () => {
    awaitingReady.current = false;
    if (captureReadyRef.current && talkingRef.current) ws.setState("listening");
  };
  wsOpts.onError = (_code: string, message: string) => {
    // Failed Standard turns cannot retain capture or accept a late response.
    // Realtime owns its own error teardown through onControl.
    if (talkMode !== "realtime") stopVoice();
    setModeError(message);
  };
  wsOpts.onAudioStart = (rate: number) => {
    if (!responseExpected.current || awaitingReady.current) return;
    // A response can only begin after our utterance has ended. Ignore late
    // response frames while recording the replacement utterance.
    if (captureReadyRef.current && (talkMode === "hold" || segmenter.current.active)) return;
    acceptPlayback.current = true;
    audio.setPlaybackRate(rate);
  };
  wsOpts.onAudioFrame = (pcm: ArrayBuffer) => {
    if (!acceptPlayback.current) return;
    if (pendingLatencyStart.current != null) {
      setLatencyMs(Math.round(performance.now() - pendingLatencyStart.current));
      pendingLatencyStart.current = null;
    }
    ws.setState("speaking");
    audio.queuePlayback(pcm);
  };
  wsOpts.onAudioEnd = (followUp: boolean) => {
    if (!acceptPlayback.current) return;
    responseExpected.current = false;
    acceptPlayback.current = false;
    audio.resetPlayback();
    setFollowUpListening(followUp);
  };

  const handleConnect = useCallback(
    (url: string, dev: string, token: string) => {
      credential.current = token;
      setModeLoaded(false);
      setConnectionEpoch(epoch => epoch + 1);
      setWsUrl(url);

      setDeviceId(dev);
      ws.connect(url, dev, token);
    },
    [ws],
  );

  const stopVoice = useCallback(() => {
    captureGeneration.current++;
    captureReadyRef.current = false;
    talkingRef.current = false;
    segmenter.current.reset();
    acceptPlayback.current = false;
    responseExpected.current = false;
    setTalking(false);
    setInputLevel(0);
    pendingLatencyStart.current = null;
    setFollowUpListening(false);
    audio.stopCapture(); audio.stopPlayback(); realtime.stop();
    ws.sendJson(ABORT_MESSAGE);
    if (ws.state !== "disconnected") ws.setState("connected");
  }, [audio, realtime, ws]);

  const stopVoiceRef = useRef(stopVoice);
  stopVoiceRef.current = stopVoice;

  useEffect(() => {
    if (!deviceId) return;
    const controller = new AbortController();
    const refresh = async (event?: Event) => {
      const detail = (event as CustomEvent | undefined)?.detail;
      if (event && (detail?.source === "talk" ||
          (detail?.scope !== "global" && detail?.deviceId !== deviceId))) return;
      const attempt = ++modeRequest.current;
      if (!event) setModeBusy(true);
      try {
        const response = await ownerFetch(`/api/devices/${encodeURIComponent(deviceId)}/speech`, { signal: controller.signal });
        if (!response.ok) throw new Error("Unable to load device voice mode");
        const data = await response.json();
        if (attempt !== modeRequest.current || controller.signal.aborted) return;
        const current = talkModeRef.current;
        const standard = event && current !== "realtime" ? current
          : localStorage.getItem(`vauxr-talk-${deviceId}`) === "toggle" ? "toggle" : "hold";
        const next = data.voice?.mode === "realtime" ? "realtime" : standard;
        if (next !== current) {
          stopVoiceRef.current();
          setTalkMode(next);
        }
        setModeLoaded(true);
        setModeError("");
      } catch (error) {
        if (!controller.signal.aborted) setModeError(String(error));
      } finally { if (attempt === modeRequest.current && !controller.signal.aborted) setModeBusy(false); }
    };
    void refresh();
    window.addEventListener("speech-settings-changed", refresh);
    return () => { controller.abort(); modeRequest.current++; window.removeEventListener("speech-settings-changed", refresh); };
  }, [deviceId, connectionEpoch]);

  const changeMode = async (next: TalkMode) => {
    if (modeBusy || next === talkMode) return;
    stopVoice();
    setModeBusy(true); setModeError("");
    const attempt = ++modeRequest.current;
    try {
      const response = await ownerFetch(`/api/devices/${encodeURIComponent(deviceId)}/speech`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: next === "realtime" ? "realtime" : "standard" }),
      });
      if (!response.ok) throw new Error("Unable to save voice mode");
      if (attempt !== modeRequest.current) return;
      setTalkMode(next);
      setModeLoaded(true);
      if (next !== "realtime") localStorage.setItem(`vauxr-talk-${deviceId}`, next);
      window.dispatchEvent(new CustomEvent("speech-settings-changed", { detail: { source: "talk", deviceId } }));
    } catch (error) { if (attempt === modeRequest.current) setModeError(String(error)); }
    finally { if (attempt === modeRequest.current) setModeBusy(false); }
  };
  useEffect(() => realtime.volume(outputVolume, outputMuted), [outputVolume, outputMuted, realtime.state]);

  const startActualTalking = useCallback(async () => {
    if (talkingRef.current) return;
    const attempt = ++captureGeneration.current;
    talkingRef.current = true;
    captureReadyRef.current = false;
    setTalking(true);
    setFollowUpListening(false);
    setModeError("");
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(
          "mic unavailable: page must be served over HTTPS or localhost (insecure context)",
        );
      }
      await audio.startCapture();
      if (attempt !== captureGeneration.current) return;
      captureReadyRef.current = true;
      if (talkMode === "hold") {
        acceptPlayback.current = false;
        audio.stopPlayback();
        awaitingReady.current = true;
        responseExpected.current = false;
        ws.sendVoiceStart();
      }
      ws.setState("listening");
    } catch (err) {
      if (attempt !== captureGeneration.current) return;
      const msg = err instanceof Error ? err.message : String(err);
      setModeError(`Microphone capture failed: ${msg}`);
      ws.addLog("sys", `Microphone capture failed: ${msg}`);
      audio.stopCapture();
      responseExpected.current = false;
      segmenter.current.reset();
      acceptPlayback.current = false;
      captureGeneration.current++;
      captureReadyRef.current = false;
      talkingRef.current = false;
      setTalking(false);
      ws.setState("connected");
    }
  }, [ws, audio, talkMode]);

  const stopActualTalking = useCallback(() => {
    if (!talkingRef.current) return;
    captureGeneration.current++;
    talkingRef.current = false;
    setTalking(false);
    audio.stopCapture();
    if (!captureReadyRef.current) return;
    captureReadyRef.current = false;
    responseExpected.current = true;
    ws.sendJson({ type: "voice.end" });
    ws.setState("processing");
    pendingLatencyStart.current = performance.now();
  }, [ws, audio]);

  const handleTalkStart = useCallback(async () => {
    if (talkMode === "realtime") {
      if (realtime.state === "stopped") await realtime.start(deviceId, credential.current);
      else realtime.stop();
      return;
    }
    if (talkMode === "toggle") {
      if (talkingRef.current) stopVoice();
      else await startActualTalking();
      return;
    }
    await startActualTalking();
  }, [talkMode, startActualTalking, stopVoice, realtime, deviceId]);

  const handleTalkEnd = useCallback(() => {
    if (talkMode !== "hold") return;
    stopActualTalking();
  }, [talkMode, stopActualTalking]);

  const handleInterrupt = useCallback(() => {
    responseExpected.current = false;
    acceptPlayback.current = false;
    audio.stopPlayback();
    ws.sendJson(ABORT_MESSAGE);
    ws.setState("connected");
    ws.addLog("sys", "Playback interrupted");
  }, [audio, ws]);

  const handleSetVolume = useCallback(
    (value: number) => {
      setOutputVolumeState(value);
      audio.setOutputVolume(value);
      if (outputMuted && value > 0) {
        setOutputMutedState(false);
        audio.setMuted(false);
      }
    },
    [audio, outputMuted],
  );

  const handleToggleMute = useCallback(() => {
    setOutputMutedState((m) => {
      const next = !m;
      audio.setMuted(next);
      return next;
    });
  }, [audio]);

  const isConnected = (CONNECTED_STATES as readonly string[]).includes(
    ws.state,
  );
  const micUnavailable =
    typeof window !== "undefined" &&
    (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia);

  const sectionContent = renderSection(activeSection, {
    isConnected,
    onConnect: handleConnect,
    onDisconnect: ws.disconnect,
    wsUrl,
    wsToken: "",
    wsState: "connected",
    addLog: ws.addLog,
  });

  return (
    <Layout
      sidebar={
        <Sidebar
          active={activeSection}
          onSelect={setActiveSection}
          connectionState={ws.state}
          deviceId={deviceId}
        />
      }
      main={
        <ResizableSplit
          top={
            <div className="flex h-full min-h-0 flex-col gap-5 overflow-y-auto px-6 py-6">
              {micUnavailable && <MicWarning />}
              <div hidden={activeSection !== "connection"}>
                <ConfigPanel
                  connected={isConnected}
                  onConnect={handleConnect}
                  onDisconnect={ws.disconnect}
                />
                <AccessPanel />
              </div>
              {activeSection !== "connection" && sectionContent}
            </div>
          }
          bottom={
            <div className="flex h-full min-h-0 flex-col">
              <div className="border-b border-white/5 px-6 py-2">
                <StatusBar state={ws.state} transcript={transcript} />
              </div>
              <div className="min-h-0 flex-1">
                <EventLog entries={ws.log} onClear={ws.clearLog} />
              </div>
            </div>
          }
        />
      }
      talk={<div className="h-full">
        {(modeError || realtime.error) && <p role="alert">{modeError || realtime.error}</p>}
        <TalkPanel
          modeBusy={modeBusy || (!!deviceId && !modeLoaded)}
          transcript={talkMode === "realtime" ? realtime.transcript : transcript}
          status={talkMode === "realtime" ? realtime.state === "listening" ? "Listening — speak naturally to interrupt." : realtime.state : undefined}
          connectionState={ws.state}
          isConnected={isConnected}
          micUnavailable={micUnavailable}
          talking={talkMode === "realtime" ? realtime.state !== "stopped" : talking}
          followUpListening={followUpListening}
          inputLevel={inputLevel}
          outputVolume={outputVolume}
          outputMuted={outputMuted}
          talkMode={talkMode}
          latencyMs={latencyMs}
          onTalkStart={handleTalkStart}
          onTalkEnd={handleTalkEnd}
          onSetVolume={handleSetVolume}
          onToggleMute={handleToggleMute}
          onSetTalkMode={changeMode}
          onInterrupt={handleInterrupt}
        />
      </div>}
    />
  );
}

interface SectionProps {
  isConnected: boolean;
  onConnect: (url: string, deviceId: string, token: string) => void;
  onDisconnect: () => void;
  wsUrl: string;
  wsToken: string;
  wsState: ReturnType<typeof useWebSocket>["state"];
  addLog: ReturnType<typeof useWebSocket>["addLog"];
}

function renderSection(id: SectionId, props: SectionProps) {
  switch (id) {
    case "connection":
      return (
        <ConfigPanel
          connected={props.isConnected}
          onConnect={props.onConnect}
          onDisconnect={props.onDisconnect}
        />
      );
    case "agents":
      return <AgentsPanel />;
    case "devices":
      return (
        <DevicesPanel
          wsUrl={props.wsUrl}
          token={props.wsToken}
          wsState={props.wsState}
          addLog={props.addLog}
        />
      );
    case "settings":
      return (
        <SettingsPanel
          wsUrl={props.wsUrl}
          token={props.wsToken}
          wsState={props.wsState}
          addLog={props.addLog}
        />
      );
  }
}

function MicWarning() {
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
      <p>
        <strong className="font-semibold">Microphone unavailable.</strong> Voice
        input requires a secure context. Load this page over HTTPS or via{" "}
        <code className="font-mono text-amber-100">http://localhost</code> —
        browsers block{" "}
        <code className="font-mono text-amber-100">getUserMedia</code> on plain
        HTTP origins like{" "}
        <code className="font-mono text-amber-100">{window.location.host}</code>
        . HTTP administration remains available.
      </p>
    </div>
  );
}
