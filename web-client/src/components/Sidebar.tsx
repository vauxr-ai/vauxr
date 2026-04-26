import Icon, { type IconName } from "./Icon";
import type { ConnectionState } from "../hooks/useWebSocket";

export type SectionId =
  | "connection"
  | "channels"
  | "devices"
  | "api"
  | "settings";

interface NavItem {
  id: SectionId;
  label: string;
  icon: IconName;
}

const NAV_ITEMS: NavItem[] = [
  { id: "connection", label: "Connection", icon: "connection" },
  { id: "channels", label: "Channels", icon: "channels" },
  { id: "devices", label: "Devices", icon: "devices" },
  { id: "api", label: "HTTP API", icon: "api" },
  { id: "settings", label: "Settings", icon: "settings" },
];

interface Props {
  active: SectionId;
  onSelect: (id: SectionId) => void;
  connectionState: ConnectionState;
  deviceId: string;
}

const CONNECTED_STATES: ConnectionState[] = [
  "connected",
  "listening",
  "processing",
  "speaking",
];

export default function Sidebar({
  active,
  onSelect,
  connectionState,
  deviceId,
}: Props) {
  const isConnected = CONNECTED_STATES.includes(connectionState);

  return (
    <aside
      aria-label="Primary navigation"
      className="flex h-full flex-col border-r border-white/5 bg-zinc-950/60 px-4 py-5 backdrop-blur-xl"
    >
      <div className="mb-8 flex items-center gap-3 px-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300 shadow-inner-border">
          <Icon name="logo" size={22} />
        </span>
        <div className="leading-tight">
          <h1 className="text-sm font-bold uppercase tracking-[0.18em] text-white">
            Vauxr
          </h1>
          <p className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
            Voice Portal
          </p>
        </div>
      </div>

      <nav aria-label="Sections" className="flex-1 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = active === item.id;
          return (
            <button
              key={item.id}
              type="button"
              aria-current={isActive ? "page" : undefined}
              onClick={() => onSelect(item.id)}
              className={`focus-ring group relative flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                isActive
                  ? "bg-indigo-500/10 text-indigo-300"
                  : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
              }`}
            >
              {isActive && (
                <span
                  aria-hidden
                  className="absolute inset-y-1 left-0 w-[2px] rounded-full bg-indigo-400 shadow-[0_0_10px_rgba(99,102,241,0.7)]"
                />
              )}
              <Icon
                name={item.icon}
                size={18}
                className={
                  isActive
                    ? "text-indigo-300"
                    : "text-zinc-500 group-hover:text-zinc-300"
                }
              />
              <span className="font-medium">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto space-y-2 border-t border-white/5 pt-4">
        <div
          className={`pill ${
            isConnected
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
              : "border-zinc-700/50 bg-zinc-800/40 text-zinc-400"
          }`}
        >
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${
              isConnected
                ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]"
                : "bg-zinc-500"
            }`}
          />
          {isConnected ? "Connected" : "Disconnected"}
        </div>
        {isConnected && deviceId && (
          <p
            className="px-1 text-[11px] text-zinc-500 truncate"
            title={deviceId}
          >
            {deviceId}
          </p>
        )}
      </div>
    </aside>
  );
}
