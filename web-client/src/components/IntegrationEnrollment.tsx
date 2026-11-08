import RefreshButton from "./RefreshButton";
import { useState } from "react";
import { ownerPost } from "../auth/api";

interface Request {
  request_id: string;
  agent_id: string;
  origin: string;
  server_id: string;
  display_name: string;
  state: string;
  expires_at: number;
}

export default function IntegrationEnrollment({compact = false}: {compact?: boolean}) {
  const [page, setPage] = useState(0);
  const [requests, setRequests] = useState<Request[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    const result = await ownerPost("/api/integrations/v1/list");
    setRequests(result.requests);
    setPage(value => Math.min(value, Math.max(0, result.requests.length - 1)));
    setLoaded(true);
  }
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Integration request failed. Refresh before retrying.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="Integration enrollment" className="space-y-3">
      <h3>Approve an integration</h3>
      {(!compact || !requests.length) && <EnrollmentIntroduction/>}
      <RefreshButton label="Refresh integration requests" busy={busy} onClick={() => run(refresh)} />
      {busy && <p role="status">Checking integration request…</p>}
      {error && <p role="alert">{error} Refresh requests to reconcile a possibly accepted approval before retrying.</p>}
      {loaded && requests.length === 0 && <p>No integration requests. Start enrollment in the OpenClaw client, then refresh.</p>}
      {compact && requests.length > 1 && <nav className="enrollment-pages" aria-label="Integration requests">
        <button type="button" disabled={busy || page === 0} onClick={() => setPage(value => value - 1)}>Previous request</button>
        <span>{Math.min(page + 1, requests.length)} / {requests.length}</span>
        <button type="button" disabled={busy || page >= requests.length - 1} onClick={() => setPage(value => value + 1)}>Next request</button>
      </nav>}
      {(compact ? requests.slice(Math.min(page, requests.length - 1), Math.min(page, requests.length - 1) + 1) : requests).map(request => (
        <RequestRow key={`${request.request_id}-${request.state}`} request={request} busy={busy} compact={compact}
          act={(action, code) => run(async () => {
            await ownerPost(`/api/integrations/v1/${action}`, {
              request_id: request.request_id,
              ...(action === "approve" ? { user_code: code } : {}),
            });
            await refresh();
          })} />
      ))}
      {(!compact || !requests.length) && <EnrollmentOutcome/>}
    </section>
  );
}

function EnrollmentIntroduction() { return <p>
        Start setup in your OpenClaw integration, then refresh here. Match the request ID,
        server address and eight-character code shown by that client. Names are untrusted
        and may repeat. Approve only a setup you deliberately started.
      </p>; }

function EnrollmentOutcome() { return <p>Approval lets the client receive its own credential once. Access starts only after
        its save acknowledgement. Then refresh routing agents and explicitly set it as default.
        Rotate or revoke existing access in Pairing and access.</p>; }

function RequestRow({ request, busy, act, compact }: {
  compact: boolean;
  request: Request;
  busy: boolean;
  act: (action: "approve" | "deny", code?: string) => Promise<void>;
}) {
  const [step, setStep] = useState(0);
  const steps = ["Introduction", "Request identity", "Granted permissions", "Access limits", "Confirm matching setup"];
  const [code, setCode] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const live = ["pending", "approved", "delivered"].includes(request.state)
    && request.expires_at * 1000 > Date.now();
  async function submit(action: "approve" | "deny") {
    const submitted = code;
    setCode("");
    setConfirmed(false);
    await act(action, submitted);
  }
  return (
    <section aria-label={`Integration request ${request.request_id}`} className="border-t space-y-2 py-3">
      {compact && <nav className="enrollment-pages" aria-label="Enrollment setup steps">
        <button type="button" disabled={busy || step === 0} onClick={() => setStep(value => value - 1)}>Back</button>
        <strong aria-live="polite">{step + 1} / {steps.length} · {steps[step]}</strong>
        <button type="button" disabled={busy || step === steps.length - 1} onClick={() => setStep(value => value + 1)}>Next</button>
      </nav>}
      {compact && step === 0 && <><EnrollmentIntroduction/><EnrollmentOutcome/></>}
      {compact && step !== 1 && <p>{request.display_name}</p>}
      {(!compact || step === 1) && <>
      <p>{request.display_name} — {request.request_id}</p>
      <p>Server: {request.origin} — {request.server_id}</p>
      <p>Agent: {request.agent_id}</p>
      <p>State: {request.state}. Deadline: {new Date(request.expires_at * 1000).toLocaleString()}</p>
      {request.state === "delivered" && <p>Delivery attempted; durable save has NOT been acknowledged.</p>}
      {request.state === "completed" && <p>Client acknowledged saving; ready for explicit default-agent selection.</p>}
      </>}
      {live && request.state === "pending" && <>
        {(!compact || step === 4) && <label>Integration matching code
          <input value={code} autoComplete="off" spellCheck={false} maxLength={8}
            onChange={event => setCode(event.target.value.toUpperCase())} />
        </label>}
        {(!compact || step === 2 || step === 3) && <section aria-label="Integration permissions" className="space-y-2">
          {(!compact || step === 2) && <>
          <p>Approving grants this integration fixed permissions after it acknowledges
            durable credential save, even while its agent is inactive:</p>
          <ul className="list-disc pl-5">
            <li>List devices, send device announcements, and issue allowed device controls.</li>
            <li>Initiate firmware updates, including OTA.</li>
            <li>Initiate and approve physical device pairing. Each pairing approval still requires
              a fresh verified physical pairing window and confirmation of the matching code
              spoken by the device.</li>
            <li>Connect to its own agent and respond to existing voice requests on that
              agent when active.</li>
          </ul>
          <p>Setting the default agent selects fallback voice routing; it does not grant these permissions
            or gate device access. Device playback is reserved; no playback URL endpoint is available.</p>
          </>}
          {(!compact || step === 3) && <>
          <p>This does not grant owner administration; credential creation, disclosure, rotation
            or revocation; device configuration, button mappings or speech/provider settings;
            agent listing or configuration; webhook configuration; server management;
            firmware image reading, upload or publication; or device impersonation.</p>
          <p>It cannot approve browser enrollment or known-device recovery, choose a replacement
            device key, or receive a device credential.</p>
          </>}
        </section>}
        {(!compact || step === 4) && <>
        <label><input type="checkbox" checked={confirmed}
          onChange={event => setConfirmed(event.target.checked)} />
          I started this setup and matched the request, server and code in my client
        </label>
        <button disabled={busy || !confirmed || !/^[0-9A-F]{8}$/.test(code)}
          onClick={() => void submit("approve")}>Approve matching integration</button>
        </>}
      </>}
      {live && (!compact || step === 1) && <button disabled={busy} onClick={() => {
        if (confirm(`Deny integration request ${request.request_id}?`)) void submit("deny");
      }}>Deny integration request</button>}
    </section>
  );
}
