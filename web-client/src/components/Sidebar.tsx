import Appearance, { useAppearance } from "./Appearance";
import { useOwnerActions } from "../auth/OwnerGate";
import Icon, { type IconName } from "./Icon";
import type { ConnectionState } from "../hooks/useWebSocket";

export type SectionId = "agents" | "devices" | "settings";

interface NavItem {
  id: SectionId;
  label: string;
  icon: IconName;
}

const NAV_ITEMS: NavItem[] = [
  { id: "agents", label: "Agents", icon: "agents" },
  { id: "devices", label: "Devices", icon: "devices" },
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
  const { theme, setTheme } = useAppearance();
  const { logout, busy, error } = useOwnerActions();
  const isConnected = CONNECTED_STATES.includes(connectionState);

  return (
    <aside aria-label="Primary navigation" className="app-sidebar">
      <div className="mb-8 flex items-center gap-3 px-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300 shadow-inner-border">
          <Icon name="logo" size={22} />
        </span>
        <div className="leading-tight">
          <h1 className="text-sm font-bold uppercase tracking-[0.18em] text-white">
            Vauxr
          </h1>
          <p className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">
            Voice & devices
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

      <div className="sidebar-footer space-y-3">
        <Appearance theme={theme} onChange={setTheme} />
        <button className="logout" disabled={busy} onClick={logout}>
          Log out on this browser
        </button>
        {error && <p role="alert">{error}</p>}
        <div
          className={`pill ${
            isConnected
              ? "connection-status-connected"
              : "border-zinc-700/50 bg-zinc-800/40 text-zinc-400"
          }`}
        >
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${
              isConnected
                ? "connection-status-dot"
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
