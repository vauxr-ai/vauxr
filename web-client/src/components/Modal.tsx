import { useEffect, useRef, useId, type ReactNode, type RefObject } from "react";

/** Native modal supplies inert background, focus containment and Escape handling. */
export default function Modal({
  title,
  children,
  onClose,
  returnFocus,
  titleContent, headerAction, className = "",
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  returnFocus: RefObject<HTMLButtonElement>;
  titleContent?: ReactNode; headerAction?: ReactNode; className?: string;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => {
      element.close();
      returnFocus.current?.focus();
    };
  }, [returnFocus]);
  return (
    <dialog
      ref={dialog}
      className={`modal auth-panel ${className}`}
      aria-labelledby={titleContent ? undefined : titleId}
      aria-label={titleContent ? title : undefined}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(
          dialog.current!.querySelectorAll<HTMLElement>(
            'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [href], [tabindex="0"]',
          ),
        ).filter((el) => el.tabIndex >= 0 && el.getClientRects().length > 0);
        const first = controls[0],
          last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header className="modal-heading">
        <div>
          <span className="eyebrow">MAKE IT YOURS</span>
          <div className="modal-title-line"><h2 id={titleId}>{titleContent || title}</h2>{headerAction}</div>
        </div>
        <button type="button" aria-label="Close dialog" onClick={onClose}>
          ×
        </button>
      </header>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}
