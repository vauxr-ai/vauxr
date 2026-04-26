import { useCallback, useMemo, useRef, useState } from "react";
import Layout from "./components/Layout";
import Sidebar, { type SectionId } from "./components/Sidebar";
import TalkPanel, { type TalkMode } from "./components/TalkPanel";
import ResizableSplit from "./components/ResizableSplit";
import StatusBar from "./components/StatusBar";
import EventLog from "./components/EventLog";
import ConfigPanel from "./components/ConfigPanel";
import ChannelsPanel from "./components/ChannelsPanel";
import DevicesPanel from "./components/DevicesPanel";
import HttpApiPanel from "./components/HttpApiPanel";
import SettingsPanel from "./components/SettingsPanel";
import { useWebSocket } from "./hooks/useWebSocket";
import { useAudio } from "./hooks/useAudio";

const CONNECTED_STATES = ["connected", "listening", "processing", "speaking"] as const;

export default function App() {
  const [transcript, setTranscript] = useState("");
  const [talking, setTalking] = useState(false);
  const [followUpListening, setFollowUpListening] = useState(false);
  const talkingRef = useRef(false);
  const [wsUrl, setWsUrl] = useState("");
  const [wsToken, setWsToken] = useState("");
  const [deviceId, setDeviceId] = useState("");

  const [activeSection, setActiveSection] = useState<SectionId>("connection");
  const [talkMode, setTalkMode] = useState<TalkMode>("hold");
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
          setLatencyMs(Math.round(performance.now() - pendingLatencyStart.current));
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
  const audio = useAudio({
    onPcmChunk: useCallback(
      (pcm: Int16Array) => {
        if (talkingRef.current) ws.sendAudioFrame(pcm);
      },
      [ws],
    ),
    onInputLevel: useCallback((level: number) => {
      setInputLevel(level);
    }, []),
  });

  // Patch the memoized opts to use live refs
  wsOpts.onAudioFrame = (pcm: ArrayBuffer) => {
    if (pendingLatencyStart.current != null) {
      setLatencyMs(Math.round(performance.now() - pendingLatencyStart.current));
      pendingLatencyStart.current = null;
    }
    ws.setState("speaking");
    audio.queuePlayback(pcm);
  };
  wsOpts.onAudioEnd = (followUp: boolean) => {
    audio.resetPlayback();
    setFollowUpListening(followUp);
  };

  const handleConnect = useCallback(
    (url: string, dev: string, token: string) => {
      setWsUrl(url);
      setWsToken(token);
      setDeviceId(dev);
      ws.connect(url, dev, token);
    },
    [ws],
  );

  const startActualTalking = useCallback(async () => {
    talkingRef.current = true;
    setTalking(true);
    setFollowUpListening(false);
    ws.sendVoiceStart();
    ws.setState("listening");
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error(
          "mic unavailable: page must be served over HTTPS or localhost (insecure context)",
        );
      }
      await audio.startCapture();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      ws.addLog("sys", `startCapture failed: ${msg}`);
      talkingRef.current = false;
      setTalking(false);
      ws.setState("connected");
    }
  }, [ws, audio]);

  const stopActualTalking = useCallback(() => {
    if (!talkingRef.current) return;
    talkingRef.current = false;
    setTalking(false);
    audio.stopCapture();
    ws.sendJson({ type: "voice.end" });
    ws.setState("processing");
    pendingLatencyStart.current = performance.now();
  }, [ws, audio]);

  const handleTalkStart = useCallback(async () => {
    if (talkMode === "toggle") {
      if (talkingRef.current) stopActualTalking();
      else await startActualTalking();
      return;
    }
    await startActualTalking();
  }, [talkMode, startActualTalking, stopActualTalking]);

  const handleTalkEnd = useCallback(() => {
    if (talkMode === "toggle") return;
    stopActualTalking();
  }, [talkMode, stopActualTalking]);

  const handleInterrupt = useCallback(() => {
    audio.stopPlayback();
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

  const isConnected = (CONNECTED_STATES as readonly string[]).includes(ws.state);
  const micUnavailable =
    typeof window !== "undefined" &&
    (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia);

  const sectionContent = renderSection(activeSection, {
    isConnected,
    onConnect: handleConnect,
    onDisconnect: ws.disconnect,
    wsUrl,
    wsToken,
    wsState: ws.state,
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
            <div className="flex h-full flex-col gap-5 px-6 py-6">
              {micUnavailable && <MicWarning />}
              {sectionContent}
            </div>
          }
          bottom={
            <div className="flex h-full min-h-0 flex-col">
              <div className="border-b border-white/5 px-6 py-2">
                <StatusBar state={ws.state} transcript={transcript} />
              </div>
              <div className="min-h-0 flex-1">
                <EventLog entries={ws.log} />
              </div>
            </div>
          }
        />
      }
      talk={
        <TalkPanel
          connectionState={ws.state}
          isConnected={isConnected}
          micUnavailable={micUnavailable}
          talking={talking}
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
          onSetTalkMode={setTalkMode}
          onInterrupt={handleInterrupt}
        />
      }
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
    case "channels":
      return (
        <ChannelsPanel
          wsUrl={props.wsUrl}
          token={props.wsToken}
          wsState={props.wsState}
          addLog={props.addLog}
        />
      );
    case "devices":
      return (
        <DevicesPanel
          wsUrl={props.wsUrl}
          token={props.wsToken}
          wsState={props.wsState}
          addLog={props.addLog}
        />
      );
    case "api":
      return (
        <HttpApiPanel
          wsUrl={props.wsUrl}
          token={props.wsToken}
          wsState={props.wsState}
          addLog={props.addLog}
        />
      );
    case "settings":
      return <SettingsPanel />;
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
        <code className="font-mono text-amber-100">{window.location.host}</code>.
      </p>
    </div>
  );
}
