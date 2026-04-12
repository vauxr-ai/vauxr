import type { ConnectionState } from "../hooks/useWebSocket";

const STATUS_COLORS: Record<ConnectionState, string> = {
  disconnected: "bg-gray-500",
  connected: "bg-green-500",
  listening: "bg-yellow-400",
  processing: "bg-blue-500",
  speaking: "bg-purple-500",
};

const STATUS_LABELS: Record<ConnectionState, string> = {
  disconnected: "Disconnected",
  connected: "Connected",
  listening: "Listening",
  processing: "Processing",
  speaking: "Speaking",
};

export default function StatusBar({
  state,
  transcript,
}: {
  state: ConnectionState;
  transcript: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <span className={`inline-block h-3 w-3 rounded-full ${STATUS_COLORS[state]}`} />
      <span className="text-sm font-medium">{STATUS_LABELS[state]}</span>
      {transcript && (
        <span className="ml-4 text-sm text-gray-400 italic truncate max-w-md">
          &ldquo;{transcript}&rdquo;
        </span>
      )}
    </div>
  );
}
