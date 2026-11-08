import { useState } from "react";
import Icon from "./Icon";
export default function RefreshButton({ label = "Refresh", busy = false, disabled = false, onClick }: {
  label?: string; busy?: boolean; disabled?: boolean; onClick: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  return <button type="button" className="refresh-button" aria-label={label} title={label}
    aria-busy={busy || pending} disabled={disabled || busy || pending}
    onClick={async () => { setPending(true); try { await onClick(); } finally { setPending(false); } }}>
    <Icon name="refresh" size={16} aria-hidden="true" />
  </button>;
}
