import Icon from "./Icon";

interface Props {
  /** Volume in [0, 1]. */
  value: number;
  muted: boolean;
  onChange: (value: number) => void;
  onToggleMute: () => void;
}

export default function VolumeSlider({
  value,
  muted,
  onChange,
  onToggleMute,
}: Props) {
  const pct = Math.round((muted ? 0 : value) * 100);
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between px-0.5 text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-500">
        <span>Output volume</span>
        <span className={muted ? "text-zinc-600" : "text-zinc-300"}>{pct}%</span>
      </div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          aria-label={muted ? "Unmute output" : "Mute output"}
          aria-pressed={muted}
          onClick={onToggleMute}
          className={`focus-ring flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/5 bg-white/5 transition-colors hover:bg-white/10 ${
            muted ? "text-amber-300" : "text-zinc-300"
          }`}
        >
          <Icon name={muted ? "volume-off" : "volume"} size={14} />
        </button>
        <input
          aria-label="Output volume"
          type="range"
          min={0}
          max={100}
          step={1}
          value={pct}
          disabled={muted}
          onChange={(e) => onChange(Number(e.target.value) / 100)}
          className="vauxr-volume-range h-1.5 w-full cursor-pointer appearance-none rounded-full bg-zinc-800 accent-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            background: muted
              ? "rgb(39 39 42)"
              : `linear-gradient(to right, rgb(99 102 241) 0%, rgb(99 102 241) ${pct}%, rgb(39 39 42) ${pct}%, rgb(39 39 42) 100%)`,
          }}
        />
      </div>
    </div>
  );
}
