import TalkButton from "./TalkButton";
import MicLevelMeter from "./MicLevelMeter";
import VolumeSlider from "./VolumeSlider";
import Icon from "./Icon";
import type { ConnectionState } from "../hooks/useWebSocket";

export type TalkMode = "hold" | "toggle";

interface Props {
  connectionState: ConnectionState;
  isConnected: boolean;
  micUnavailable: boolean;
  talking: boolean;
  followUpListening: boolean;
  inputLevel: number;
  outputVolume: number;
  outputMuted: boolean;
  talkMode: TalkMode;
  latencyMs: number | null;
  onTalkStart: () => void;
  onTalkEnd: () => void;
  onSetVolume: (value: number) => void;
  onToggleMute: () => void;
  onSetTalkMode: (mode: TalkMode) => void;
  onInterrupt: () => void;
}

const STATE_HELPER: Record<ConnectionState, string> = {
  disconnected: "Connect a server to start talking.",
  connected: "Hold the button or press Spacebar.",
  listening: "Vauxr is listening — release to send.",
  processing: "Thinking…",
  speaking: "Vauxr is responding.",
};

export default function TalkPanel(props: Props) {
  const {
    connectionState,
    isConnected,
    micUnavailable,
    talking,
    followUpListening,
    inputLevel,
    outputVolume,
    outputMuted,
    talkMode,
    latencyMs,
    onTalkStart,
    onTalkEnd,
    onSetVolume,
    onToggleMute,
    onSetTalkMode,
    onInterrupt,
  } = props;

  const buttonDisabled =
    !isConnected ||
    micUnavailable ||
    connectionState === "speaking" ||
    connectionState === "processing";

  const helper = !isConnected
    ? STATE_HELPER.disconnected
    : STATE_HELPER[connectionState];

  return (
    <aside
      aria-label="Talk panel"
      className="flex h-full flex-col border-l border-white/5 bg-zinc-950/40 backdrop-blur-sm"
    >
      <div className="flex flex-1 flex-col items-center justify-center gap-8 px-5 py-6">
        <div className="flex flex-col items-center gap-5">
          <TalkButton
            disabled={buttonDisabled}
            active={talking}
            processing={connectionState === "processing"}
            speaking={connectionState === "speaking"}
            followUp={
              followUpListening &&
              !talking &&
              connectionState !== "processing" &&
              connectionState !== "speaking"
            }
            onTalkStart={onTalkStart}
            onTalkEnd={onTalkEnd}
          />
          <p
            data-testid="talk-helper"
            className="text-center text-[11px] uppercase tracking-[0.2em] text-zinc-500"
          >
            {helper}
          </p>
        </div>

        {connectionState === "speaking" && (
          <button
            type="button"
            onClick={onInterrupt}
            className="focus-ring inline-flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-200 hover:bg-amber-500/20"
          >
            <Icon name="stop" size={14} />
            Interrupt
          </button>
        )}
      </div>

      <div className="space-y-5 border-t border-white/5 px-5 py-5">
        <div className="space-y-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">
            Talk mode
          </span>
          <div
            role="radiogroup"
            aria-label="Talk mode"
            className="flex rounded-lg bg-zinc-900/80 p-1 text-xs shadow-inner-border"
          >
            {(["hold", "toggle"] as const).map((m) => (
              <button
                key={m}
                role="radio"
                aria-checked={talkMode === m}
                onClick={() => onSetTalkMode(m)}
                className={`focus-ring flex-1 rounded-md px-2 py-1.5 font-medium capitalize transition-colors ${
                  talkMode === m
                    ? "bg-indigo-500/20 text-indigo-200 shadow-inner-border"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {m === "hold" ? "Hold to talk" : "Toggle"}
              </button>
            ))}
          </div>
        </div>

        <MicLevelMeter level={inputLevel} active={talking} />

        <VolumeSlider
          value={outputVolume}
          muted={outputMuted}
          onChange={onSetVolume}
          onToggleMute={onToggleMute}
        />

        <div className="flex items-center justify-between border-t border-white/5 pt-3 text-[10px] uppercase tracking-[0.2em] text-zinc-500">
          <span>Latency</span>
          <span
            className={
              latencyMs == null ? "text-zinc-600" : "font-mono text-zinc-300"
            }
          >
            {latencyMs == null ? "—" : `${latencyMs} ms`}
          </span>
        </div>
      </div>
    </aside>
  );
}
