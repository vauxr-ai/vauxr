import { useCallback, useEffect, useRef } from "react";

interface Props {
  disabled: boolean;
  active: boolean;
  onTalkStart: () => void;
  onTalkEnd: () => void;
}

export default function TalkButton({
  disabled,
  active,
  onTalkStart,
  onTalkEnd,
}: Props) {
  const held = useRef(false);

  const start = useCallback(() => {
    if (disabled || held.current) return;
    held.current = true;
    onTalkStart();
  }, [disabled, onTalkStart]);

  const end = useCallback(() => {
    if (!held.current) return;
    held.current = false;
    onTalkEnd();
  }, [onTalkEnd]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.code === "Space" && !e.repeat && e.target === document.body) {
        e.preventDefault();
        start();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && e.target === document.body) {
        e.preventDefault();
        end();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [start, end]);

  return (
    <button
      className={`h-28 w-28 rounded-full text-sm font-semibold uppercase tracking-wider transition-all select-none ${
        disabled
          ? "bg-gray-700 text-gray-500 cursor-not-allowed"
          : active
            ? "bg-red-500 scale-110 shadow-lg shadow-red-500/40 text-white"
            : "bg-indigo-600 hover:bg-indigo-500 text-white cursor-pointer"
      }`}
      disabled={disabled}
      onMouseDown={start}
      onMouseUp={end}
      onMouseLeave={end}
      onTouchStart={start}
      onTouchEnd={end}
    >
      {active ? "Release" : "Hold to Talk"}
    </button>
  );
}
