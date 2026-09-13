import { useEffect, useState } from "react";
import { jsonResponse, ownerFetch, ownerPost, randomId } from "../auth/api";
interface Pair {
  request_id: string;
  device_id: string;
  kind: string;
  display_name: string;
  status: string;
  expires_at: number;
}
interface Operation {
  operation_id: string;
  role: string;
  subject: string;
  action: string;
  state?: string;
  expires_at?: number;
  overlap_until?: number;
}
const stateHelp: Record<string, string> = {
  queued: "Queued; may be offline. No replacement saved.",
  pending: "Client observed the request; not proof it is online or saved.",
  delivered: "Delivery attempted; durable save has NOT been acknowledged.",
  acknowledged: "Client acknowledged durable save; predecessor access retired.",
  completed: "Completed after durable-save acknowledgement.",
  expired:
    "Expired. After unacknowledged delivery, re-pair/recover is required.",
  revoked: "Access revoked. Re-pair required; configuration retained.",
};
export default function AccessPanel() {
  const [pairs, setPairs] = useState<Pair[]>([]);
  const [devices, setDevices] = useState<
    { id: string; name: string; state?: string }[]
  >([]);
  const [channels, setChannels] = useState<{ id: string; name: string }[]>([]);
  const [operations, setOperations] = useState<Operation[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem("vauxr-operations") || "[]");
    } catch {
      return [];
    }
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [subject, setSubject] = useState("");
  const [role, setRole] = useState("device");
  const retain = (rows: Operation[]) => {
    sessionStorage.setItem("vauxr-operations", JSON.stringify(rows));
    setOperations(rows);
  };
  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    const p = await ownerPost("/api/enrollment/v1/list");
    setPairs(p.requests);
    const d = await jsonResponse(await ownerFetch("/api/devices"));
    setDevices(Array.isArray(d) ? d : d.devices);
    setChannels(await jsonResponse(await ownerFetch("/api/channels")));
  }
  useEffect(() => {
    void run(refresh);
    const changed = () => {
      try {
        setOperations(
          JSON.parse(sessionStorage.getItem("vauxr-operations") || "[]"),
        );
      } catch {
        setError("Cannot read retained lifecycle operations.");
      }
    };
    window.addEventListener("lifecycle-operation", changed);
    return () => window.removeEventListener("lifecycle-operation", changed);
  }, []);
  async function operate(
    action: string,
    selectedRole: string,
    selectedSubject: string,
    retry?: Operation,
  ) {
    if (
      !retry &&
      !confirm(
        `${action} ${selectedRole} ${selectedSubject}? ${action === "rotate" ? "Offline rotation remains queued until the client saves and acknowledges." : "This immediately retires access; settings are preserved."}`,
      )
    )
      return;
    const op = retry || {
      operation_id: randomId(),
      role: selectedRole,
      subject: selectedSubject,
      action,
    };
    const rows = retry ? operations : [...operations, op];
    retain(rows);
    const result = await ownerPost(`/api/lifecycle/v1/${action}`, {
      operation_id: op.operation_id,
      role: op.role,
      subject: op.subject,
    });
    retain(
      rows.map((row) => (row.operation_id === op.operation_id ? result : row)),
    );
  }
  return (
    <div className="auth-panel card space-y-4 p-5">
      <h2>Pairing and access</h2>
      <p>
        Add a speaker: deliberately open its physical pairing window and listen
        to the eight digits spoken by that speaker. Refresh, identify its
        request, and enter those digits. Names are untrusted and may repeat. No
        hardware credentials are displayed here.
      </p>
      <button disabled={busy} onClick={() => run(refresh)}>
        Refresh pairing and identities
      </button>
      {error && <p role="alert">{error}</p>}
      {pairs
        .filter((p) => p.kind === "physical")
        .map((p) => (
          <PairRow
            key={p.request_id}
            pair={p}
            busy={busy}
            act={(action, code) =>
              run(async () => {
                await ownerPost(`/api/enrollment/v1/${action}`, {
                  request_id: p.request_id,
                  ...(code ? { code } : {}),
                });
                await refresh();
              })
            }
          />
        ))}
      <h3>Device credentials</h3>
      {devices.map((d) => (
        <div key={d.id}>
          <span>
            {d.name} — {d.id} — {d.state || "connectivity unknown"}{" "}
          </span>
          {["rotate", "revoke", "recover"].map((a) => (
            <button
              className="m-2"
              disabled={busy}
              key={a}
              onClick={() => run(() => operate(a, "device", d.id))}
            >
              {a} device
            </button>
          ))}
        </div>
      ))}
      <h3>Integration credentials</h3>
      <p>
        New connections require owner-approved integration enrollment. Existing integration
        rotation/revocation uses lifecycle v1; it never reveals a channel token.
      </p>
      {channels.map((c) => (
        <div key={c.id}>
          {c.name} — {c.id}
          {["rotate", "revoke"].map((a) => (
            <button
              className="m-2"
              disabled={busy}
              key={a}
              onClick={() => run(() => operate(a, "integration", c.id))}
            >
              {a} integration
            </button>
          ))}
        </div>
      ))}
      <details>
        <summary>Known offline identity</summary>
        <p>
          The device list may omit a never-connected or retired client. Enter
          its exact retained subject ID; display names are not IDs.
        </p>
        <select
          aria-label="Identity role"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option>device</option>
          <option>integration</option>
        </select>
        <input
          aria-label="Subject ID"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        {["rotate", "revoke", ...(role === "device" ? ["recover"] : [])].map(
          (a) => (
            <button
              key={a}
              disabled={busy || !subject}
              onClick={() => run(() => operate(a, role, subject))}
            >
              {a} known identity
            </button>
          ),
        )}
      </details>
      <h3>Retained operations in this tab</h3>
      <p>
        Refresh status after errors or lost replies; retry uses the same
        operation ID. No automatic retry can claim completion. Recovery grants
        last five minutes; offline rotations expire after 24 hours, and delivery
        starts a maximum five-minute save deadline.
      </p>
      {operations.map((op) => (
        <div key={op.operation_id} className="border-t border-white/10 py-3">
          <p>
            {op.role} {op.subject}: {op.action} —{" "}
            {op.state || "outcome unknown"}
          </p>
          <p>{stateHelp[op.state || ""]}</p>
          <small>
            {op.operation_id}
            {op.expires_at
              ? ` · expires ${new Date(op.expires_at * 1000).toLocaleString()}`
              : ""}
            {op.overlap_until
              ? ` · save deadline ${new Date(op.overlap_until * 1000).toLocaleString()}`
              : ""}
          </small>
          <div>
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const result = await ownerPost("/api/lifecycle/v1/status", {
                    operation_id: op.operation_id,
                  });
                  retain(
                    operations.map((o) =>
                      o.operation_id === op.operation_id ? result : o,
                    ),
                  );
                })
              }
            >
              Refresh status
            </button>{" "}
            <button
              disabled={busy}
              onClick={() =>
                run(() => operate(op.action, op.role, op.subject, op))
              }
            >
              Retry same operation
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
function PairRow({
  pair: p,
  busy,
  act,
}: {
  pair: Pair;
  busy: boolean;
  act: (action: string, code?: string) => void;
}) {
  const [code, setCode] = useState("");
  const [physical, setPhysical] = useState(false);
  const expired = p.expires_at <= Date.now() / 1000;
  return (
    <div className="border-t border-white/10 py-3">
      <p>
        {p.display_name} — {p.device_id}
      </p>
      <small>
        Request {p.request_id} · {expired ? "expired" : p.status} · expires{" "}
        {new Date(p.expires_at * 1000).toLocaleTimeString()}
      </small>
      {!expired && ["ready", "initiated"].includes(p.status) && (
        <>
          <label className="block">
            Spoken eight-digit code
            <input
              inputMode="numeric"
              autoComplete="off"
              maxLength={8}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <label className="block">
            <input
              type="checkbox"
              checked={physical}
              onChange={(e) => setPhysical(e.target.checked)}
            />
            I opened the intended speaker's physical window and heard this code
            from it.
          </label>
          <button
            disabled={busy || !physical || !/^[0-9]{8}$/.test(code)}
            onClick={() => {
              act(p.status === "ready" ? "initiate" : "approve", code);
              setCode("");
              setPhysical(false);
            }}
          >
            {p.status === "ready"
              ? "Initiate matching speaker"
              : "Approve matching speaker"}
          </button>
        </>
      )}
      {!expired &&
        ["challenge", "ready", "initiated", "approved"].includes(p.status) && (
          <button disabled={busy} onClick={() => act("deny")}>
            Deny request
          </button>
        )}
      {p.status === "consumed" && (
        <p>
          Credential consumed by client; this does not prove installation or
          physical connection.
        </p>
      )}
    </div>
  );
}
