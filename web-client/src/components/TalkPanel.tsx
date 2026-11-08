import TalkButton from "./TalkButton";
import MicLevelMeter from "./MicLevelMeter";
import VolumeSlider from "./VolumeSlider";
import Icon from "./Icon";
import type { ConnectionState } from "../hooks/useWebSocket";

export type TalkMode = "hold" | "toggle" | "realtime";

interface Props {
  compact?: boolean;
  noVoice?: boolean;
  transcript?: string;
  status?: string;
  modeBusy?: boolean;
  availableModes?: string[];
  voiceConflict?: boolean;
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
  connecting: "Connecting browser voice…",
  disconnected: "Connect browser voice, then choose when to listen.",
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

  if (props.noVoice) return (
    <aside aria-label="Talk panel" className="talk-panel talk-chat-only">
      <p role="status">Chat-only · Voice unavailable. Use text below.</p>
    </aside>
  );

  const buttonDisabled =
    !isConnected ||
    micUnavailable ||
    props.modeBusy === true ||
    props.voiceConflict === true;

  const helper = !isConnected
    ? STATE_HELPER.disconnected
    : props.status ||
      (connectionState === "processing" || connectionState === "speaking"
        ? STATE_HELPER[connectionState]
        : talkMode !== "hold"
          ? talking
            ? "Listening continuously — speak to interrupt."
            : "Click to start listening."
          : STATE_HELPER[connectionState]);

  return (
    <aside
      aria-label="Talk panel"
      className={`talk-panel flex flex-col ${props.compact ? "talk-compact" : ""}`}
    >
      <div
        hidden={props.compact && !isConnected}
        className="voice-stage flex flex-col items-center justify-center gap-8"
      >
        <div className="flex flex-col items-center gap-5">
          <TalkButton
            toggle={talkMode !== "hold"}
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

      <details className="voice-transcript w-full rounded-lg border border-white/5 p-3 text-sm">
        <summary>Transcript (optional)</summary>
        <p
          aria-live="polite"
          className="max-h-40 overflow-y-auto break-words text-zinc-300"
        >
          {props.transcript || "Your transcript will appear here."}
        </p>
      </details>
      <div className="voice-settings space-y-5 border-t border-white/5 px-5 py-5">
        <div className="space-y-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">
            Talk mode
          </span>
          <div
            role="radiogroup"
            aria-label="Talk mode"
            className="flex flex-col rounded-lg bg-zinc-900/80 p-1 text-xs shadow-inner-border"
          >
            {(["hold", "toggle", "realtime"] as const).map((m) => (
              <button
                key={m}
                role="radio"
                disabled={
                  props.modeBusy ||
                  !isConnected ||
                  (props.availableModes &&
                    !props.availableModes.includes(
                      m === "realtime" ? "realtime" : "standard",
                    ))
                }
                aria-checked={talkMode === m}
                onClick={() => onSetTalkMode(m)}
                className={`focus-ring flex-1 rounded-md px-2 py-1.5 font-medium capitalize transition-colors ${
                  talkMode === m
                    ? "bg-indigo-500/20 text-indigo-200 shadow-inner-border"
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {m === "hold"
                  ? "Standard push-to-talk"
                  : m === "toggle"
                    ? "Standard hands-free"
                    : "Realtime"}
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
