import { useState } from "react";
import { ownerPost } from "../auth/api";

interface Request {
  request_id: string;
  channel_id: string;
  origin: string;
  server_id: string;
  display_name: string;
  state: string;
  expires_at: number;
}

export default function IntegrationEnrollment() {
  const [requests, setRequests] = useState<Request[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    const result = await ownerPost("/api/integrations/v1/list");
    setRequests(result.requests);
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
      <p>
        Start setup in your OpenClaw integration, then refresh here. Match the request ID,
        server address and eight-character code shown by that client. Names are untrusted
        and may repeat. Approve only a setup you deliberately started.
      </p>
      <button disabled={busy} onClick={() => void run(refresh)}>Refresh integration requests</button>
      {error && <p role="alert">{error}</p>}
      {requests.map(request => (
        <RequestRow key={request.request_id} request={request} busy={busy}
          act={(action, code) => run(async () => {
            await ownerPost(`/api/integrations/v1/${action}`, {
              request_id: request.request_id,
              ...(action === "approve" ? { user_code: code } : {}),
            });
            await refresh();
          })} />
      ))}
      <p>Approval lets the client receive its own credential once. Access starts only after
        its save acknowledgement. Then refresh routing channels and explicitly activate it.
        Rotate or revoke existing access in Pairing and access.</p>
    </section>
  );
}

function RequestRow({ request, busy, act }: {
  request: Request;
  busy: boolean;
  act: (action: "approve" | "deny", code?: string) => Promise<void>;
}) {
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
      <p>{request.display_name} — {request.request_id}</p>
      <p>Server: {request.origin} — {request.server_id}</p>
      <p>Channel: {request.channel_id}</p>
      <p>State: {request.state}. Deadline: {new Date(request.expires_at * 1000).toLocaleString()}</p>
      {request.state === "delivered" && <p>Delivery attempted; durable save has NOT been acknowledged.</p>}
      {request.state === "completed" && <p>Client acknowledged saving; ready for explicit route activation.</p>}
      {live && request.state === "pending" && <>
        <label>Integration matching code
          <input value={code} autoComplete="off" spellCheck={false} maxLength={8}
            onChange={event => setCode(event.target.value.toUpperCase())} />
        </label>
        <label><input type="checkbox" checked={confirmed}
          onChange={event => setConfirmed(event.target.checked)} />
          I started this setup and matched the request, server and code in my client
        </label>
        <button disabled={busy || !confirmed || !/^[0-9A-F]{8}$/.test(code)}
          onClick={() => void submit("approve")}>Approve matching integration</button>
      </>}
      {live && <button disabled={busy} onClick={() => {
        if (confirm(`Deny integration request ${request.request_id}?`)) void submit("deny");
      }}>Deny integration request</button>}
    </section>
  );
}
