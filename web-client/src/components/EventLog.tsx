import { useEffect, useRef } from "react";
import type { LogEntry } from "../hooks/useWebSocket";

const DIR_STYLE: Record<LogEntry["dir"], string> = {
  tx: "text-green-400",
  rx: "text-cyan-400",
  sys: "text-gray-500",
};

const DIR_LABEL: Record<LogEntry["dir"], string> = {
  tx: "TX",
  rx: "RX",
  sys: "--",
};

export default function EventLog({ entries }: { entries: LogEntry[] }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries.length]);

  return (
    <div className="flex-1 overflow-y-auto rounded bg-gray-900 p-3 font-mono text-xs leading-5 min-h-0">
      {entries.map((e, i) => (
        <div key={i} className="whitespace-pre-wrap break-all">
          <span className="text-gray-600">
            {new Date(e.ts).toLocaleTimeString()}
          </span>{" "}
          <span className={DIR_STYLE[e.dir]}>[{DIR_LABEL[e.dir]}]</span>{" "}
          {e.text}
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
