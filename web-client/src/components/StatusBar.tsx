import type { ConnectionState } from "../hooks/useWebSocket";

const STATUS_CONFIG: Record<
  ConnectionState,
  { label: string; pillClass: string; dotClass: string }
> = {
  disconnected: {
    label: "Disconnected",
    pillClass: "border-zinc-700/50 bg-zinc-800/40 text-zinc-400",
    dotClass: "bg-zinc-500",
  },
  connected: {
    label: "Idle",
    pillClass: "border-indigo-500/30 bg-indigo-500/10 text-indigo-200",
    dotClass: "bg-indigo-400 shadow-[0_0_8px_rgba(99,102,241,0.7)]",
  },
  listening: {
    label: "Listening",
    pillClass: "border-red-500/30 bg-red-500/10 text-red-200",
    dotClass: "bg-red-400 shadow-[0_0_8px_rgba(239,68,68,0.7)]",
  },
  processing: {
    label: "Thinking",
    pillClass: "border-amber-500/30 bg-amber-500/10 text-amber-200",
    dotClass: "bg-amber-400 shadow-[0_0_8px_rgba(245,158,11,0.7)]",
  },
  speaking: {
    label: "Speaking",
    pillClass: "border-violet-500/30 bg-violet-500/10 text-violet-200",
    dotClass: "bg-violet-400 shadow-[0_0_8px_rgba(124,58,237,0.7)]",
  },
};

export default function StatusBar({
  state,
  transcript,
}: {
  state: ConnectionState;
  transcript: string;
}) {
  const cfg = STATUS_CONFIG[state];
  return (
    <div className="flex items-center gap-3 px-1 py-1.5">
      <span className={`pill ${cfg.pillClass}`}>
        <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${cfg.dotClass}`} />
        {cfg.label}
      </span>
      {transcript ? (
        <span className="truncate text-xs italic text-zinc-400">
          &ldquo;{transcript}&rdquo;
        </span>
      ) : (
        <span className="text-xs text-zinc-600">No transcript yet</span>
      )}
    </div>
  );
}
