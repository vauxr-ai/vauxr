# HTTP API reference

The control plane listens on port **8080** by default and serves the built portal
at the same authority. Paths below are relative to that authority; deployment
ports/origins can differ. Device WebSocket transport is separately available on
port 8765 at `/ws`. See the [interface reference](reference.md) for wire formats
and [configuration](configuration.md) for settings schemas.

This page describes the checked-in routes, not the legacy Postman collection.
The core route/handler authority is [web/server.py](../src/vauxr/web/server.py);
[policy.py](../src/vauxr/auth/policy.py) defines operation grants. Specialized
handlers linked below own their field validators.

## Authentication and errors

- **Owner:** same-origin HttpOnly session cookie, not a bearer operator token.
  Mutations require the exact configured `Origin` and `X-CSRF-Token` returned by
  login/session. Login exchanges the operator token for the session. Exact
  Host/scheme and trusted-proxy rules apply; see [origin setup](how-to.md#browser-on-another-machine-or-address).
- **Integration:** `Authorization: Bearer <vx_int_credential>` after enrollment,
  durable credential save and ACK. Only explicitly granted operations work;
  this is not owner management access. HTTPS-configured deployments reject
  insecure integration requests.
- **Device:** scoped identity for its own transport and authenticated firmware
  reads, not general management. `DEVICE_TOKEN` and old channel tokens grant no
  access. Keep IDs opaque; names are display metadata.
- **Delivery recipient:** a one-use firmware URL is its own capability.

Send JSON bodies with `Content-Type: application/json` unless a route specifies
binary input. Core handlers generally return JSON, with `{"error": "…"}` on
failure: 400 invalid input, 401 missing/invalid authentication, 403 denied
operation, 404 unavailable resource, 409 state conflict and 503 unavailable
service/storage. Errors are route-specific; do not assume all share a schema.
A success receipt does not prove audible playback or completed firmware install.
After a lost response or 503, refresh state before retrying a mutation: it may
already have had durable or external effects.

## Devices

Here **O** means owner, **I** enrolled integration, and **D** device credential.
All `{device_id}` values are opaque IDs from inventory/enrollment.

| Method | Path | Caller | Result / request |
| --- | --- | --- | --- |
| GET | `/api/devices` | O, I | Array of device records: `id`, `name`, `state`, `lastSeen`, `config`; optional platform/firmware metadata |
| GET | `/api/devices/{device_id}/metrics` | O | Metrics; `?window=1h\|24h\|7d\|30d`, default `24h` |
| GET | `/api/devices/{device_id}/metrics/{window}` | O | Same metrics, explicit window in path |
| PATCH | `/api/devices/{device_id}` | O | Partial configuration; returns updated device record |
| POST | `/api/devices/{device_id}/announce` | O, I | `{"text":"Dinner is ready"}`; returns `{"ok":true}` after announcement handling |
| POST | `/api/devices/{device_id}/command` | O, I | `{"command":"mute"}`; generally returns `{"ok":true}` after dispatch |

Device patches accept `name` (string), `voice` (boolean), `follow_up_mode`
(`auto`, `always`, `never`), `barge_in` (boolean), `output_sample_rate`
(16000, 22050, 24000, 44100 Hz, or null), and validated `button_actions`.
`voice_mode` accepts `websocket`, `webrtc`, `realtime`; its derived fields take
precedence over legacy `transport_mode` (`websocket`, `webrtc`) and
`pipeline_mode` (`standard`, `realtime`) in the same patch. WebSocket transport
forces barge-in off. See [device validators](../src/vauxr/devices/config.py)
for gesture/action shapes. Speech selections have their own routes below.

Announcements return 409 for a listening/processing or chat-only device and
503 when the selected speech provider is unavailable. Commands are `set_volume`,
`mute`, `unmute`, `reboot`, `ota`, `set_barge_in`. Optional `params` are forwarded
to the device except `set_barge_in`, which requires `{"enabled":true}` and updates
server configuration, returning `ok` and `barge_in`. `ota` requires
`{"url":"https://example.invalid/firmware.bin"}` in `params`, and checks the
firmware-initiation grant in addition to control. Device firmware owns the
remaining command parameter semantics; dispatch is not completion confirmation.

## Agents (not legacy channels)

These are **owner-only** routes. `/api/channels` is not registered; use
`/api/agents` for routing inventory, and versioned enrollment/lifecycle for
credentials. An unknown `/api/*` path returns 404, not a compatibility alias.

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/api/agents` | Array with identity, definition, name, type, active state, creation time and public integration/avatar metadata |
| PATCH | `/api/agents/{agent_id}` | Exactly `{"name":"New name"}`; returns `id` and trimmed `name` |
| POST | `/api/agents/{agent_id}/activate` | Select default route; `{"ok":true}`; 409 for incompatible speech fallback |
| DELETE | `/api/agents/{agent_id}` | Remove inactive agent; `{"removed":true}`; active/protected bundled agents return 409 |
| GET | `/api/agents/{agent_id}/avatar` | PNG binary, or 404 |
| POST | `/api/agents/{agent_id}/avatar` | Raw PNG/JPEG/WebP, up to 2 MiB and 2048×2048; returns avatar metadata |
| POST | `/api/agents` | Reserved legacy credential creation: 501 for owner |
| POST | `/api/agents/{agent_id}/rotate` | Reserved legacy rotation: 501 for owner |

Removal may partially commit before a 503; refresh before retrying. It retires
integration authority as well as routing; see [removal](../src/vauxr/agents/removal.py).
An integration cannot list/manage agents merely because it can list devices.

## Webhooks

All routes require an owner session. Stored authorization is never returned.
See [handlers](../src/vauxr/web/server.py) and [URL validation/storage](../src/vauxr/web/webhooks.py).

| Method | Path | Contract |
| --- | --- | --- |
| GET | `/api/webhooks` | Public list metadata, not editable secrets |
| GET | `/api/webhooks/{webhook_id}` | Editing detail including URL/body, without authorization secret; no-store |
| POST | `/api/webhooks` | Required `name`, `url`; optional `authorization`, JSON `body` |
| PATCH | `/api/webhooks/{webhook_id}` | Partial update of the same fields |
| DELETE | `/api/webhooks/{webhook_id}` | Delete named hook |
| POST | `/api/webhooks/{webhook_id}/duplicate` | Copy URL, authorization and body with a unique copy name |

## Firmware delivery

| Method | Path | Caller / contract |
| --- | --- | --- |
| GET | `/firmware/{filename}` | O or D; authenticated binary read |
| POST | `/api/firmware-delivery/{filename}` | O or I; mint URL for an existing `.bin`; no JSON payload required |
| GET | `/firmware-delivery/{token}/{filename}` | One-use capability download; no Authorization header required |

The mint handler is guarded by `FIRMWARE_DELIVERY_MINT`, granted to both owner
and integration by current policy (despite an older owner-only implementation
comment). Owner calls still require Origin/CSRF. Firmware files are resolved
under `DATA_DIR/firmware`, with filename validation; see
[delivery implementation](../src/vauxr/devices/firmware.py).

Mint returns `201` with `{"url": "<configured owner origin>/firmware-delivery/<token>/<filename>",
"expires_in": 120}`. The configured owner origin must be reachable by the device.
The URL serves the binary directly, without redirects or query/header authentication.
Delivery tokens are random (256 bits), stored only as hashes in process memory, bound
to the exact filename, and expire using a monotonic clock. At most 128 can be live;
minting returns `503` when full, and expired entries are reclaimed on mint.
Restarting the process invalidates all outstanding URLs. Route mint and download to
the same server process if using multiple workers.

A redemption attempt consumes the token before opening/streaming the file, including
filename mismatches and failed downloads. Retry requires a newly minted URL; HEAD
cannot redeem it. Invalid, expired, replayed, mismatched and missing artifacts return
the same `404`. Non-regular files, symlinks and traversal are rejected. Responses are
`no-store`. Treat the URL as a temporary bearer secret: do not log or persist it.
The server disables aiohttp access logs; reverse proxies must also disable or redact
request paths for `/firmware-delivery/` and must not cache these responses.

## Owner sessions and versioned provisioning

| Method | Path | Actions and authority |
| --- | --- | --- |
| GET | `/api/auth/{action}` | `status`, `session`; session returns `csrf_token` and `expires_at` for a valid cookie |
| POST | `/api/auth/{action}` | `claim` (`code`), `save` (`save_acknowledgement`, `saved`), `login` (`operator_token`), `logout` (empty object) |
| POST | `/api/enrollment/v1/{action}` | Device request/proof/redemption and approval; owner-only `prompts` / `save-prompts` |
| POST | `/api/integrations/v1/{action}` | Integration enrollment: client request/status/delivery/cancel/ACK and owner approval |
| POST | `/api/lifecycle/v1/{action}` | Credential rotation/revoke and polling; authority depends on action/proof |

Provisioning requires the configured authority, JSON objects without duplicate
keys, no query string, at most 4096 body bytes and a 10-second body-read deadline.
Owner-cookie mutations require Origin/CSRF; client proof flows are not owner
sessions. Integration enrollment rejects Authorization headers and owner cookies
on its client actions. Do not attach a generic bearer/cookie to every flow.
Rate limits/capacity can return 429; conflict/unavailable states can return 409.
Credential delivery must be saved durably before ACK.

The [versioned contract map](reference.md#versioned-provisioning-contracts) links
exact request fields and state validators. HTTP authorities are
[owner](../src/vauxr/web/owner.py), [device enrollment](../src/vauxr/provisioning/enrollment_http.py),
[integration enrollment](../src/vauxr/provisioning/integration_http.py) and
[lifecycle](../src/vauxr/provisioning/lifecycle_http.py). Follow those validators
rather than inferring action payloads from a path template.

## Speech, settings and portal services

These routes require the owner session. This inventory links specialized
contracts instead of duplicating their schemas.

| Methods | Paths | Contract authority |
| --- | --- | --- |
| GET, PATCH | `/api/speech`, `/api/devices/{device_id}/speech`, `/api/agents/{agent_id}/speech` | [Speech selections](../src/vauxr/web/speech.py), [configuration](configuration.md#speech-providers-and-selections) |
| POST | `/api/speech/preview` | [Preview input/output](../src/vauxr/web/speech_preview.py); requires separately enrolled browser identity |
| GET | `/api/settings/providers`, `/api/settings/speech` | [Settings handlers](../src/vauxr/settings/http.py) |
| PUT | `/api/settings/providers/profiles/{profile_id}`, `/api/settings/providers/speech-auth`, `/api/settings/speech/{backend_id}` | Same settings validators; secrets are not reflected |
| POST | `/api/settings/providers/profiles/{profile_id}/discover`, `/api/settings/speech/{backend_id}/discover` | Explicit provider discovery; may contact configured service |
| GET | `/api/settings/plugins` | [Plugin settings](../src/vauxr/plugins/settings.py) |
| PUT | `/api/settings/plugins/{plugin_id}` | [Plugin settings validators](../src/vauxr/plugins/settings.py) |
| GET | `/api/plugins`, `/api/plugins/openclaw/package` | [Distribution catalog and gzip package](../src/vauxr/plugins/__init__.py) |
| GET, POST | `/api/support` | [Support installation/configuration](../src/vauxr/agents/support.py) |
| GET, POST | `/api/local-agents`, `/api/local-agents/{agent_id}/runtime` | [Local agent configuration/runtime](../src/vauxr/agents/local.py) |
| DELETE | `/api/local-agents/{agent_id}` | Same local-agent handler |
| GET, POST | `/api/interface` | [Static interface read/save](../src/vauxr/interface.py); save uses exact revision |
| POST | `/api/interface/preview` | Sanitized preview, not publication |
| POST | `/api/companion/{action}` | [Companion](../src/vauxr/web/companion.py): `events`, `ack`, `context`, `select`, `chat`; also needs `X-Vauxr-Browser` credential and matching `browser_id` |

The optional realtime application registers POST at the configured offer path
(default `/api/offer`); [realtime/app.py](../src/vauxr/realtime/app.py) authenticates
the scoped device and offer. It is not an owner management endpoint, nor is it
available merely because the control plane is running.

The bundled [Postman collection](../postman/vauxr.postman_collection.json) is
historical: its channel/token examples are superseded and are not the current
authentication or route contract.
