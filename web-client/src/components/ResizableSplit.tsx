import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

interface Props {
  top: ReactNode;
  bottom: ReactNode;
  /** Initial bottom-pane height in pixels. */
  initialBottom?: number;
  /** Minimum bottom-pane height in pixels. */
  minBottom?: number;
  /** Maximum bottom-pane height in pixels. */
  maxBottom?: number;
  bottomLabel?: string;
}

export default function ResizableSplit({
  top,
  bottom,
  initialBottom = 240,
  minBottom = 96,
  maxBottom = 800,
  bottomLabel = "Resize bottom pane",
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [bottomHeight, setBottomHeight] = useState(initialBottom);
  const draggingRef = useRef(false);
  const [dragging, setDragging] = useState(false);

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    setDragging(true);
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!draggingRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const next = rect.bottom - e.clientY;
      const clamped = Math.min(maxBottom, Math.max(minBottom, next));
      setBottomHeight(clamped);
    },
    [minBottom, maxBottom],
  );

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    setDragging(false);
    (e.target as HTMLElement).releasePointerCapture(e.pointerId);
  }, []);

  // Keyboard accessibility: Up/Down adjusts in 16px steps, PageUp/Down 64px.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      let delta = 0;
      if (e.key === "ArrowUp") delta = 16;
      else if (e.key === "ArrowDown") delta = -16;
      else if (e.key === "PageUp") delta = 64;
      else if (e.key === "PageDown") delta = -64;
      else return;
      e.preventDefault();
      setBottomHeight((h) =>
        Math.min(maxBottom, Math.max(minBottom, h + delta)),
      );
    },
    [minBottom, maxBottom],
  );

  // Re-clamp on container resize so it stays inside bounds when the window shrinks.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const max = Math.min(maxBottom, el.clientHeight - 80);
      setBottomHeight((h) => Math.min(h, Math.max(minBottom, max)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [minBottom, maxBottom]);

  return (
    <div ref={containerRef} className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">{top}</div>
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label={bottomLabel}
        aria-valuenow={Math.round(bottomHeight)}
        aria-valuemin={minBottom}
        aria-valuemax={maxBottom}
        tabIndex={0}
        data-testid="resize-handle"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onKeyDown={handleKeyDown}
        className={`group relative h-1.5 shrink-0 cursor-ns-resize touch-none transition-colors ${
          dragging ? "bg-indigo-500/60" : "bg-white/5 hover:bg-indigo-500/30"
        }`}
      >
        <span
          aria-hidden
          className={`pointer-events-none absolute left-1/2 top-1/2 h-[2px] w-10 -translate-x-1/2 -translate-y-1/2 rounded-full transition-colors ${
            dragging ? "bg-indigo-200" : "bg-zinc-600 group-hover:bg-indigo-300"
          }`}
        />
      </div>
      <div
        style={{ height: bottomHeight }}
        className="min-h-0 shrink-0 overflow-hidden"
      >
        {bottom}
      </div>
    </div>
  );
}
