# API reference

Reference for server source **25469df63362853a2a4daf18cc0a115c6626b6bf**.
The portal HTTP authority (normally `http://localhost:8080`) serves these routes.
This is not the device/agent WebSocket authority. IDs are opaque inventory IDs,
not display names. Bodies and responses below are JSON unless stated otherwise.

- [WebSockets, WebRTC and streaming lifecycle](api-transports.md)
- [Coverage and verification](api-coverage.md)
- [Devices, agents, webhooks and firmware](api-http-control.md)
- [Owner authentication and provisioning](api-http-auth.md)
- [Speech, settings and browser features](api-http-settings.md)
- [Registered-route coverage ledger](https://github.com/vauxr-ai/vauxr/blob/docs/complete-api-reference/docs/site/api-http-coverage.json)

## Authentication and shared failures

**Owner** means a logged-in same-origin session, not an operator bearer token.
Use the exact configured owner HTTP/HTTPS origin, including port. HTTPS uses
`__Host-vauxr_owner`; LAN HTTP uses `vauxr_owner`. Both are HttpOnly, SameSite=Strict,
path `/`. Mutations with the owner cookie need the exact `Origin` and
`X-CSRF-Token` returned by login or `GET /api/auth/session`. Do not send both cookie
names. A cross-origin development server cannot authenticate owner requests.
Forwarding is accepted only under the configured trusted-proxy contract.

**Integration** means `Authorization: Bearer <enrolled-integration-credential>`.
Only explicitly marked operations accept it. **Device** credentials cannot manage
other devices. Legacy shared tokens grant no owner access. Neither credential nor
cookie values belong in logs or examples. For browser-bound operations,
`X-Vauxr-Browser` carries this browser's enrolled device credential *in addition*
to the owner session; a submitted device ID never grants authority.

Policy-wrapped routes return `401 {"error":"unauthorized"}` for no principal,
`403 {"error":"forbidden"}` for denied operations. Owner boundary failures use
JSON `error` (`forbidden`, `invalid_request`, `owner_unavailable`), with 403 or
503 as applicable. Endpoint-specific errors are listed on each page. Do not
assume every framework error is JSON: unwrapped aiohttp exceptions can be text
(e.g. avatar size limits or speech authorization without an owner cookie).
Uncaught infrastructure failures are not a stable application error schema.

## Safe request example

After logging into the owner portal, run this in its developer console. It uses
the current same-origin cookie automatically and does not embed credentials:

```js
const session = await fetch('/api/auth/session').then(r => r.json());
const response = await fetch('/api/agents/<agent-id>', {
  method: 'PATCH',
  headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token},
  body: JSON.stringify({name: 'Patio helper'})
});
console.log(response.status, await response.json());
```

Replace `<agent-id>` with an ID from `GET /api/agents`. Success is
`200 {"id":"<agent-id>","name":"Patio helper"}`. Examples are illustrative,
source-checked, not executed against a live installation.

## Non-API registrations

`OPTIONS /{tail:.*}` returns 204. CORS advertises GET/POST/PATCH/DELETE/OPTIONS,
Authorization and Content-Type, max-age 86400; this is not a credentialed
cross-origin owner API and does not advertise PUT or CSRF headers.

`GET /{tail:.*}` serves `web-client/dist` files or its `index.html` SPA fallback.
Traversal is rejected. Missing distribution/index returns JSON 404; unmatched
`/api`, `/api/*`, `/firmware-delivery` and its descendants return JSON
`{"error":"not found"}` instead of the SPA. The combined server also registers
this fallback on its device/agent listener. Treat it as static delivery, not an
API. aiohttp GET registrations implicitly accept HEAD except firmware capability
download, which explicitly disables HEAD. WebSocket upgrades and configurable
realtime offer/signaling are transport reference scope, not HTTP management.

## Evidence

Registration roots: `src/vauxr/web/server.py:attach_http_routes`,
`src/vauxr/server.py`; delegated registrars and exact locations appear in the
ledger. Source links use the pinned revision in the ledger. Tests and the portal
consumer (`web-client/src`) were inspected alongside handlers; see the ledger for
checks and explicit limitations. This reference describes implemented behavior,
not a promise that optional providers or media services are available.
