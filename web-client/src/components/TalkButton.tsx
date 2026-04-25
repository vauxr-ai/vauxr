import { useCallback, useEffect, useRef } from "react";

export type TalkButtonMode =
  | "disabled"
  | "idle"
  | "listening"
  | "processing"
  | "speaking"
  | "follow-up";

interface Props {
  disabled: boolean;
  active: boolean;
  processing?: boolean;
  speaking?: boolean;
  followUp?: boolean;
  onTalkStart: () => void;
  onTalkEnd: () => void;
}

const KEYFRAMES = `
@keyframes vauxr-heartbeat {
  0%, 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.45); }
  50% { transform: scale(1.04); box-shadow: 0 0 28px 6px rgba(99, 102, 241, 0.35); }
}
@keyframes vauxr-ripple {
  0% { transform: scale(0.7); opacity: 0.65; }
  100% { transform: scale(1.9); opacity: 0; }
}
@keyframes vauxr-spin {
  to { transform: rotate(360deg); }
}
@keyframes vauxr-eq {
  0%, 100% { transform: scaleY(0.35); }
  50% { transform: scaleY(1); }
}
`;

function computeMode(props: Props): TalkButtonMode {
  if (props.disabled) return "disabled";
  if (props.active) return "listening";
  if (props.processing) return "processing";
  if (props.speaking) return "speaking";
  if (props.followUp) return "follow-up";
  return "idle";
}

const LABELS: Record<TalkButtonMode, string> = {
  disabled: "Hold to Talk",
  idle: "Hold to Talk",
  listening: "Listening...",
  processing: "Thinking...",
  speaking: "Speaking...",
  "follow-up": "Follow-up...",
};

const BASE_CLASSES =
  "relative h-28 w-28 rounded-full text-xs font-semibold uppercase tracking-wider transition-colors select-none flex items-center justify-center";

const BUTTON_CLASSES: Record<TalkButtonMode, string> = {
  disabled: "bg-gray-700 text-gray-500 cursor-not-allowed",
  idle: "bg-indigo-600 hover:bg-indigo-500 text-white cursor-pointer",
  listening: "bg-red-500 text-white scale-105 shadow-lg shadow-red-500/40",
  processing: "bg-indigo-600 text-white",
  speaking: "bg-violet-600 text-white",
  "follow-up": "bg-teal-500 text-white",
};

function Ripples({ color }: { color: string }) {
  return (
    <>
      {[0, 0.6, 1.2].map((delay, i) => (
        <span
          key={i}
          className={`pointer-events-none absolute inset-0 rounded-full ${color}`}
          style={{
            animation: `vauxr-ripple 1.8s ease-out ${delay}s infinite`,
          }}
        />
      ))}
    </>
  );
}

function Spinner() {
  return (
    <span
      className="pointer-events-none absolute inset-1 rounded-full border-2 border-transparent border-t-white/90 border-r-white/40"
      style={{ animation: "vauxr-spin 1s linear infinite" }}
    />
  );
}

function Equalizer() {
  const bars = [0, 1, 2, 3, 4, 5, 6];
  return (
    <>
      <div className="pointer-events-none absolute -left-10 top-1/2 -translate-y-1/2 flex h-16 items-center gap-1">
        {bars.map((i) => (
          <span
            key={`l-${i}`}
            className="w-1 rounded-full bg-violet-300"
            style={{
              height: "100%",
              transformOrigin: "center",
              animation: `vauxr-eq 0.7s ease-in-out ${(i % 4) * 0.1}s infinite`,
            }}
          />
        ))}
      </div>
      <div className="pointer-events-none absolute -right-10 top-1/2 -translate-y-1/2 flex h-16 items-center gap-1">
        {bars.map((i) => (
          <span
            key={`r-${i}`}
            className="w-1 rounded-full bg-violet-300"
            style={{
              height: "100%",
              transformOrigin: "center",
              animation: `vauxr-eq 0.7s ease-in-out ${(i % 4) * 0.13 + 0.05}s infinite`,
            }}
          />
        ))}
      </div>
    </>
  );
}

export default function TalkButton(props: Props) {
  const { disabled, onTalkStart, onTalkEnd } = props;
  const held = useRef(false);
  const mode = computeMode(props);

  const start = useCallback(() => {
    if (disabled || held.current) return;
    held.current = true;
    onTalkStart();
  }, [disabled, onTalkStart]);

  const end = useCallback(() => {
    if (!held.current) return;
    held.current = false;
    onTalkEnd();
  }, [onTalkEnd]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && e.target === document.body) {
        e.preventDefault();
        start();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && e.target === document.body) {
        e.preventDefault();
        end();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [start, end]);

  const heartbeatStyle =
    mode === "idle"
      ? { animation: "vauxr-heartbeat 2.4s ease-in-out infinite" }
      : undefined;

  return (
    <div className="relative">
      <style>{KEYFRAMES}</style>

      {mode === "listening" && <Ripples color="bg-red-500/40" />}
      {mode === "follow-up" && <Ripples color="bg-teal-400/40" />}
      {mode === "speaking" && <Equalizer />}

      <button
        className={`${BASE_CLASSES} ${BUTTON_CLASSES[mode]}`}
        style={heartbeatStyle}
        disabled={disabled}
        onMouseDown={start}
        onMouseUp={end}
        onMouseLeave={end}
        onTouchStart={start}
        onTouchEnd={end}
      >
        {mode === "processing" && <Spinner />}
        <span className="relative z-10">{LABELS[mode]}</span>
      </button>
    </div>
  );
}
