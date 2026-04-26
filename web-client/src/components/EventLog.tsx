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
      className="flex h-full flex-col gap-2 px-6 pb-4 pt-3"
    >
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="card-section-title">Event Log</span>
          <span className="h-px w-10 bg-white/10" aria-hidden />
          <span className="text-[11px] font-medium text-zinc-500">
            {entries.length} {entries.length === 1 ? "entry" : "entries"}
          </span>
        </div>
        {onClear && (
          <button
            type="button"
            onClick={onClear}
            className="focus-ring text-xs text-zinc-500 hover:text-zinc-200"
          >
            Clear
          </button>
        )}
      </header>
      <div className="card flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 font-mono text-[11px] leading-5">
          {entries.length === 0 ? (
            <p className="text-zinc-600">
              No events yet. Connect a server to begin.
            </p>
          ) : (
            entries.map((e, i) => (
              <div key={i} className="flex gap-3 whitespace-pre-wrap break-all">
                <span className="w-20 shrink-0 text-zinc-600">
                  {new Date(e.ts).toLocaleTimeString()}
                </span>
                <span
                  className={`w-8 shrink-0 font-semibold ${DIR_STYLE[e.dir]}`}
                >
                  {DIR_LABEL[e.dir]}
                </span>
                <span className="text-zinc-300">{e.text}</span>
              </div>
            ))
          )}
          <div ref={endRef} />
        </div>
      </div>
    </section>
  );
}
