# HTTP API: rename an agent

Use this representative owner operation to understand request shape, authority
and failure handling. The HTTP API is served at the portal authority, normally
`http://localhost:8080`. It is separate from the channel WebSocket port 8765.

## PATCH /api/agents/{agent_id}

Rename an existing agent instance without changing its identity or routing.
Get the opaque `agent_id` from owner inventory (`GET /api/agents`); do not use a
display name or registered type ID in its place.

Authentication requires a logged-in same-origin owner session cookie. Mutations
also require the exact configured `Origin` and `X-CSRF-Token` from the owner session
(`GET /api/auth/session` returns `csrf_token` and `expires_at`). An enrolled
integration credential cannot administer agents. No legacy shared token grants
owner access.

| Request element | Required value |
| --- | --- |
| Content-Type | `application/json` |
| Body | Exactly one field, `name` |
| `name` | Nonempty display string, 1–200 characters after trimming; no control characters |

Example request body:

```json
{"name":"Patio garden helper"}
```

Example success (`200`, illustrative opaque ID):

```json
{"id":"0123456789abcdef0123456789abcdef","name":"Patio garden helper"}
```

For a safe first exercise, rename an instance using the owner portal and inspect
that PATCH in browser developer tools. Keep cookies, CSRF values and operator
tokens out of shared captures. This example intentionally does not embed them in
a shell command or prescribe a generic bearer token.

## Failures and recovery

| Status | Meaning / next action |
| --- | --- |
| 400 | Invalid JSON, extra/missing field, or invalid name; correct the request |
| 401 | Missing or invalid owner session; log in again |
| 403 | Authority/origin/CSRF boundary rejected; use the configured owner origin |
| 404 | Agent not found; refresh inventory and use its current ID |
| 503 | Save unavailable; refresh before retrying, since persistence may be uncertain |

Handler failures use a JSON `error` string, for example
`{"error":"provide only the agent name"}` for an extra body field. Do not assume
all server routes share this operation's body or error details. Success confirms
the metadata update, not model inference or audio playback.

See [types and instances](concepts.md), [the tutorial](tutorial.md) and
[setup troubleshooting](guides.md) for the user-facing workflow.
