import { useEffect, useRef, useState, type ReactNode } from "react";

interface Props {
  chatOnly?: boolean;
  open: boolean;
  onOpen: (open: boolean) => void;
  context: string;
  route: string;
  state: string;
  connection: ReactNode;
  children: ReactNode;
}
const MOBILE_QUERY = "(max-width: 760px)";
const FOCUSABLE =
  'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, [tabindex="0"]';

export default function Companion({
  chatOnly = false,
  open,
  onOpen,
  context,
  route,
  state,
  connection,
  children,
}: Props) {
  const [mobile, setMobile] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const media = window.matchMedia?.(MOBILE_QUERY);
    const update = () => setMobile(!!media?.matches);
    update();
    media?.addEventListener("change", update);
    return () => media?.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onOpen(false);
      }
      if (event.key !== "Tab" || !window.matchMedia?.(MOBILE_QUERY).matches)
        return;
      const nodes = [
        ...(panel.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []),
      ].filter((element) => {
        if (!element.getClientRects().length) return false;
        // Closed details may retain descendant rectangles but cannot receive focus.
        for (
          let parent = element.parentElement;
          parent && parent !== panel.current;
          parent = parent.parentElement
        ) {
          if (
            parent instanceof HTMLDetailsElement &&
            !parent.open &&
            parent.querySelector(":scope > summary") !== element
          )
            return false;
        }
        return true;
      });
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", keyboard);
    return () => {
      window.removeEventListener("keydown", keyboard);
      (previous?.isConnected ? previous : trigger.current)?.focus();
    };
  }, [open, onOpen]);

  useEffect(() => {
    const media = window.matchMedia?.(MOBILE_QUERY);
    const background =
      panel.current
        ?.closest(".app-shell")
        ?.querySelectorAll<HTMLElement>(".management, .app-sidebar") ?? [];
    const previous = new Map(
      [...background].map((element) => [element, element.inert]),
    );
    const apply = () => {
      for (const element of background) {
        element.inert = previous.get(element)! || (open && !!media?.matches);
      }
    };
    apply();
    media?.addEventListener("change", apply);
    return () => {
      media?.removeEventListener("change", apply);
      for (const element of background) element.inert = previous.get(element)!;
    };
  }, [open]);

  useEffect(() => {
    const element = panel.current;
    const shell = element?.closest<HTMLElement>(".app-shell");
    if (!element || !shell) return;
    const property = "--voice-dock-reserve";
    const previous = shell.style.getPropertyValue(property);
    const measure = () => {
      // Reserve the dock's real height and bottom offset, including wrapped states.
      const reserve = open
        ? 0
        : Math.max(0, window.innerHeight - element.getBoundingClientRect().top);
      shell.style.setProperty(property, `${Math.ceil(reserve)}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
      if (previous) shell.style.setProperty(property, previous);
      else shell.style.removeProperty(property);
    };
  }, [open]);

  return (
    <section
      id="voice-companion"
      ref={panel}
      className={`companion ${open ? "is-open" : "is-collapsed"}`}
      aria-label={chatOnly ? "Text companion" : "Voice companion"}
      role={open && mobile ? "dialog" : "region"}
      aria-modal={open && mobile ? true : undefined}
    >
      <header className="companion-header">
        <div>
          <span className="eyebrow">{chatOnly ? "Text companion" : "Voice companion"}</span>
          <h2>{route}</h2>
          <p role="status">{state}</p>
        </div>
        {open ? (
          <button
            ref={close}
            onClick={() => onOpen(false)}
            aria-label={chatOnly ? "Collapse text companion" : "Collapse voice companion"}
          >
            ×
          </button>
        ) : (
          <button
            ref={trigger}
            onClick={() => onOpen(true)}
            aria-expanded={false}
            aria-controls="voice-companion"
            aria-label={chatOnly ? "Expand text companion" : "Expand voice companion"}
          >
            ↗
          </button>
        )}
      </header>
      {open && (
        <>
          <p className="context-chip">Viewing {context}</p>
          <p className="text-xs text-zinc-400">
            Browser conversation route. Manage device assignments in Devices and agent
            defaults in Agents.
          </p>
        </>
      )}
      {/* Both owners stay mounted when collapsed or navigating. */}
      <div className="companion-body">
        {children}
        <div className="companion-connection">{connection}</div>
      </div>
    </section>
  );
}
