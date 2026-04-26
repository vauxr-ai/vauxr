interface Props {
  /** Linear input level in [0, 1]. */
  level: number;
  active?: boolean;
  segments?: number;
}

/**
 * Eight-segment mic input meter. Segments light up indigo as level rises.
 * Inactive (not capturing) renders a dim row.
 */
export default function MicLevelMeter({
  level,
  active = false,
  segments = 8,
}: Props) {
  const clamped = Math.max(0, Math.min(1, level));
  const lit = active ? Math.round(clamped * segments) : 0;
  const dbDisplay = active && clamped > 0.001
    ? `${Math.max(-60, Math.round(20 * Math.log10(clamped)))} dB`
    : "—";

  return (
    <div
      className="space-y-2"
      role="meter"
      aria-label="Microphone input level"
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={Number(clamped.toFixed(2))}
    >
      <div className="flex justify-between px-0.5 text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">
        <span>Mic input</span>
        <span className={active ? "text-indigo-300" : "text-zinc-600"}>
          {dbDisplay}
        </span>
      </div>
      <div className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full bg-zinc-900/80 p-[2px] shadow-inner-border">
        {Array.from({ length: segments }).map((_, i) => {
          const on = i < lit;
          // Fade brightness as segments climb
          const opacity = on ? 1 - i * (0.45 / segments) : 0;
          return (
            <span
              key={i}
              data-testid="mic-level-segment"
              data-lit={on ? "true" : "false"}
              style={{ opacity: on ? opacity : undefined }}
              className={`h-full flex-1 rounded-[1px] ${
                on ? "bg-indigo-400" : "bg-zinc-800"
              }`}
            />
          );
        })}
      </div>
    </div>
  );
}
