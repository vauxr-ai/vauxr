import Icon from "./Icon";

export default function SettingsPanel() {
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-white/5 px-5 py-3">
        <h2 className="text-base font-semibold text-zinc-100">Settings</h2>
        <p className="text-xs text-zinc-500">
          Workspace preferences and appearance.
        </p>
      </div>
      <div className="flex items-start gap-4 px-5 py-8 text-sm text-zinc-400">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-300">
          <Icon name="settings" size={18} />
        </span>
        <div className="space-y-1">
          <p className="font-medium text-zinc-200">Coming soon.</p>
          <p className="text-xs text-zinc-500">
            Per-user preferences (theme, default device, keyboard shortcuts) will
            live here.
          </p>
        </div>
      </div>
    </div>
  );
}
