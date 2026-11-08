import type { ConnectionState } from "./useWebSocket";

export function companionStatus({
  connected,
  capturing,
  enrolling,
  state,
  realtime,
  error,
  outputMuted,
}: {
  connected: boolean;
  capturing: boolean;
  enrolling: boolean;
  state: ConnectionState;
  realtime: "stopped" | "connecting" | "listening";
  error?: string;
  outputMuted: boolean;
}) {
  let label: string;
  if (!connected && capturing) label = "Stopping voice";
  else if (error)
    label = `Connection failed · Mic ${capturing ? "active" : "off"}`;
  else if (enrolling || state === "connecting")
    label = `Connecting · Mic ${capturing ? "active" : "off"}`;
  else if (!connected) label = "Disconnected · Mic off";
  else if (state === "processing") label = "Thinking";
  else if (state === "speaking") label = "Speaking";
  else if (realtime === "connecting") label = "Starting live voice";
  else if (capturing) label = "Listening";
  else label = "Ready · Mic off";
  return label + (outputMuted ? " · Output muted" : "");
}
