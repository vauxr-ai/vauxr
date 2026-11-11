import { useEffect, useState } from "react";
import { jsonResponse, ownerFetch } from "../auth/api";
import LoadingIndicator from "./LoadingIndicator";

type Status = {
  enabled: boolean;
  acknowledged: boolean;
  environment_disabled: boolean;
  endpoint_ready: boolean;
  payload: string | null;
  payload_status: string;
  queued: number;
};
export default function TelemetrySettings() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    setError("");
    try { setStatus(await jsonResponse(await ownerFetch("/api/settings/telemetry"))); }
    catch { setError("Telemetry status unavailable. Refresh before changing your choice."); }
  }
  useEffect(() => { void refresh(); }, []);
  async function decide(enabled: boolean) {
    setBusy(true); setError("");
    try {
      setStatus(await jsonResponse(await ownerFetch("/api/settings/telemetry", {
        method: "PUT", body: JSON.stringify({ enabled, acknowledged: true }),
      })));
    } catch { setError("Choice could not be confirmed. Refresh before retrying."); }
    finally { setBusy(false); }
  }
  return <section className="card settings-form telemetry-settings">
    <span className="eyebrow">Privacy</span>
    <h2>Usage telemetry</h2>
    <p>Help improve Vauxr with hourly usage totals and a separate startup count.
      Telemetry is enabled by default, but collection and sending wait for your choice here.</p>
    <p>Reports contain a random monthly identifier, release category, OS and architecture,
      bucketed device count, feature and session counts, outcomes, uptime, and completed-session
      duration buckets—not provider latency. Custom providers and models use the custom category.</p>
    <p>No conversations, audio, prompts, responses, errors, logs, secrets, hostnames, URLs,
      device or user names, or location are sent. AWS sees the transport IP address;
      this is not a guarantee of anonymity. Raw reports expire logically after 30 days;
      broad daily totals remain without identifiers or context dimensions. Retries last up to seven days.</p>
    {!status && !error && <LoadingIndicator label="Fetching telemetry status" />}
    {error && <p role="alert">{error}</p>}
    {status && <>
      <p role="status">{status.environment_disabled ? "Disabled by server environment."
        : !status.acknowledged ? "Awaiting your choice. Nothing has been collected or sent."
        : status.enabled ? "Telemetry enabled." : "Telemetry disabled."}
        {!status.endpoint_ready && " Collector not configured; no network sends are possible."}</p>
      {status.environment_disabled ? <p>The server environment override takes precedence over this setting.</p>
        : <div className="telemetry-actions">
          <button type="button" className="button-primary" disabled={busy}
            onClick={() => void decide(true)}>{busy ? "Saving choice…" : "Enable telemetry"}</button>
          <button type="button" disabled={busy} onClick={() => void decide(false)}>
            {status.acknowledged ? "Disable telemetry" : "Keep telemetry off"}</button>
        </div>}
      <p>Disabling cancels sending and clears the retry queue and monthly identifier.</p>
      <button type="button" disabled={busy} onClick={() => void refresh()}>Refresh status and payload</button>
      {status.payload && <details open={!status.acknowledged}>
        <summary>Exact serialized payload · {status.payload_status}</summary>
        <p>This is the actual JSON serialization used for sending, including transmitted identifiers.
          Later reports reflect their own interval and counters. Queued reports: {status.queued}.</p>
        <pre className="telemetry-payload">{status.payload}</pre>
      </details>}
    </>}
    {!status && error && <button type="button" onClick={() => void refresh()}>Refresh status</button>}
  </section>;
}
