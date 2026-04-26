import { useEffect, useRef } from "react";
import type { LogEntry } from "../hooks/useWebSocket";

const DIR_STYLE: Record<LogEntry["dir"], string> = {
  tx: "text-emerald-400/90",
  rx: "text-cyan-300/90",
  sys: "text-zinc-500",
};

const DIR_LABEL: Record<LogEntry["dir"], string> = {
  tx: "TX",
  rx: "RX",
  sys: "SYS",
};

interface Props {
  entries: LogEntry[];
  onClear?: () => void;
}

export default function EventLog({ entries, onClear }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [entries.length]);

  return (
    <section
      aria-label="Event log"
      className="relative flex h-full min-h-0 flex-col border-t border-white/5"
    >
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-1 font-mono text-[11px] leading-5">
        {entries.length === 0 ? (
          <p className="text-zinc-600">No events yet. Connect a server to begin.</p>
        ) : (
          entries.map((e, i) => (
            <div key={i} className="flex gap-3 whitespace-pre-wrap break-all">
              <span className="w-20 shrink-0 text-zinc-600">
                {new Date(e.ts).toLocaleTimeString()}
              </span>
              <span className={`w-8 shrink-0 font-semibold ${DIR_STYLE[e.dir]}`}>
                {DIR_LABEL[e.dir]}
              </span>
              <span className="text-zinc-300">{e.text}</span>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
      {onClear && entries.length > 0 && (
        <button
          type="button"
          onClick={onClear}
          className="focus-ring absolute right-2 top-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-zinc-600 opacity-50 hover:bg-white/5 hover:text-zinc-200 hover:opacity-100"
        >
          Clear
        </button>
      )}
    </section>
  );
}
