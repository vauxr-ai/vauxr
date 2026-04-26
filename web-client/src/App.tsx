import { useCallback, useMemo, useRef, useState } from "react";
import ConfigPanel from "./components/ConfigPanel";
import StatusBar from "./components/StatusBar";
import TalkButton from "./components/TalkButton";
import EventLog from "./components/EventLog";
import HttpApiPanel from "./components/HttpApiPanel";
import ChannelsPanel from "./components/ChannelsPanel";
import DevicesPanel from "./components/DevicesPanel";
import { useWebSocket } from "./hooks/useWebSocket";
import { useAudio } from "./hooks/useAudio";

export default function App() {
  const [transcript, setTranscript] = useState("");
  const [talking, setTalking] = useState(false);
  const [followUpListening, setFollowUpListening] = useState(false);
  const talkingRef = useRef(false);
  const [wsUrl, setWsUrl] = useState("");
  const [wsToken, setWsToken] = useState("");

  const wsOpts = useMemo(
    () => ({
      onReady: () => {},
      onTranscript: (text: string) => setTranscript(text),
      onAudioStart: (sampleRate: number) => {
        audio.setPlaybackRate(sampleRate);
      },
      onAudioFrame: (pcm: ArrayBuffer) => {
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
  const audio = useAudio(
    useCallback(
      (pcm: Int16Array) => {
        if (talkingRef.current) ws.sendAudioFrame(pcm);
      },
      [ws],
    ),
  );

  // Patch the memoized opts to use live refs
  wsOpts.onAudioFrame = (pcm: ArrayBuffer) => {
    ws.setState("speaking");
    audio.queuePlayback(pcm);
  };
  wsOpts.onAudioEnd = (followUp: boolean) => {
    audio.resetPlayback();
    setFollowUpListening(followUp);
  };

  const handleConnect = useCallback(
    (url: string, deviceId: string, token: string) => {
      setWsUrl(url);
      setWsToken(token);
      ws.connect(url, deviceId, token);
    },
    [ws],
  );

  const handleTalkStart = useCallback(async () => {
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

  const handleTalkEnd = useCallback(() => {
    talkingRef.current = false;
    setTalking(false);
    audio.stopCapture();
    ws.sendJson({ type: "voice.end" });
    ws.setState("processing");
  }, [ws, audio]);

  const isConnected = ws.state !== "disconnected";
  const micUnavailable =
    !window.isSecureContext || !navigator.mediaDevices?.getUserMedia;

  return (
    <div className="flex h-screen flex-col gap-4 p-6 max-w-4xl mx-auto">
      <h1 className="text-lg font-bold tracking-tight">
        Vauxr Portal
      </h1>

      {micUnavailable && (
        <div className="rounded border border-amber-600/40 bg-amber-900/30 px-3 py-2 text-sm text-amber-200">
          <strong className="font-semibold">Microphone unavailable.</strong>{" "}
          Voice input requires a secure context. Load this page over HTTPS or
          via <code className="font-mono">http://localhost</code> — browsers
          block <code className="font-mono">getUserMedia</code> on plain HTTP
          origins like <code className="font-mono">{window.location.host}</code>.
        </div>
      )}

      <ConfigPanel
        connected={isConnected}
        onConnect={handleConnect}
        onDisconnect={ws.disconnect}
      />

      <StatusBar state={ws.state} transcript={transcript} />

      <div className="flex justify-center py-4">
        <TalkButton
          disabled={
            !isConnected ||
            micUnavailable ||
            ws.state === "speaking" ||
            ws.state === "processing"
          }
          active={talking}
          processing={ws.state === "processing"}
          speaking={ws.state === "speaking"}
          followUp={followUpListening && !talking && ws.state !== "processing" && ws.state !== "speaking"}
          onTalkStart={handleTalkStart}
          onTalkEnd={handleTalkEnd}
        />
      </div>

      <p className="text-xs text-gray-500 text-center">
        Hold the button or press Spacebar to talk
      </p>

      <ChannelsPanel
        wsUrl={wsUrl}
        token={wsToken}
        wsState={ws.state}
        addLog={ws.addLog}
      />

      <DevicesPanel
        wsUrl={wsUrl}
        token={wsToken}
        wsState={ws.state}
        addLog={ws.addLog}
      />

      <HttpApiPanel
        wsUrl={wsUrl}
        token={wsToken}
        wsState={ws.state}
        addLog={ws.addLog}
      />

      <EventLog entries={ws.log} />
    </div>
  );
}
