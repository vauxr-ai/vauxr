# Authorization foundation (#46)

**Stacked owner update (#47):** [owner-v1.md](owner-v1.md) supersedes this
foundation snapshot for owner setup, sessions, startup configuration and persistence
schema 2. Owner HTTP bearer admission is removed; paired-client policy is unchanged.
The descriptions below record PR55's original foundation boundary.

This is a breaking, staged foundation for issue #46, scoped by the `scope`
acceptance criteria in the September 12 work packages. It is not an owner login,
enrollment, credential lifecycle, TLS or release implementation. **Do not deploy
this branch as a standalone upgrade.** It intentionally rejects shared credentials
instead of leaving an administrative fallback while the dependent packages ship.

## Principal and policy contract

`auth.authenticate(token)` resolves a persisted verifier to immutable
`Principal(role, subject, credential_id)`. It returns no secret or verifier.
`auth_policy.allowed(principal, operation, resource=..., physical_verified=...)`
is the common decision function. All grants, including owner grants, are explicit.
Unknown operations and anonymous callers are denied. Roles are not interchangeable:
owner credentials cannot enter either device or integration voice transports.

| Capability | Owner | Integration | Device |
| --- | --- | --- | --- |
| Device list, announce, volume/mute/reboot/barge-in control | yes | yes | no |
| Update initiation (`ota` command) | yes | yes | no |
| Firmware download | yes | no | yes |
| Firmware publication, server management | reserved | no | no |
| Device settings/button mapping, routing-channel list/activation | yes | no | no |
| Webhook metadata/configuration | yes | no | no |
| Credential create/disclose/rotate/revoke; owner administration | reserved | no | no |
| Hardware pairing initiation/approval with verified physical participation | reserved | reserved | no |
| Device WS/audio/button/realtime control and offer | no | no | own identity only |
| Channel connection and voice responses | no | yes, bound routing channel | no |
| Future playback API | reserved | reserved | no |

“Reserved” means an explicit **unshipped** operation, not a working API. The
`UNSHIPPED` set names these contracts. Existing channel create/delete/rotate URLs
return 501 for an authorized owner, 403 for integrations/devices, and 401 for an
unknown credential. Their old secret-issuing bodies have been removed. There is
no new credential, owner, pairing, publication or playback endpoint in this branch.
Owner-only channel activation does not issue or change credentials.

Integration OTA permission is limited to initiating the existing device update
command; it grants no file publication, server configuration or credential access.
The existing command carries an HTTP(S) image URL. Signed firmware and authenticated
server trust are downstream requirements, not implemented here. Device firmware
fetches from this server now require the device's bearer credential.

## Identity and persistence

`DATA_DIR/authz.json` is version 1, separate from device settings and legacy routing
configuration. The internal `CredentialStore.replace()` boundary accepts immutable
`Credential(id, role, subject, verifier, enabled)` records. It is not an enrollment
API or operator CLI. Future provisioning must generate at least 256 bits of random
token entropy and persist only the SHA-256 hex verifier. This scheme is for generated
random tokens, **not human passwords**. Fixtures seed test records explicitly.

A device subject is its stable device ID, never a client-selected identity at login.
An integration subject references its routing-channel ID. Owner records share one
owner subject. IDs use 1–128 ASCII letters/digits/`_.:-`, starting with an alphanumeric.
A credential ID cannot be rebound to another subject, role or verifier through the
store replacement boundary. Rotation must allocate a new credential ID. Multiple
records may bind one subject for a future bounded overlap, but this package does not
create or enforce lifecycle transitions. Duplicate credential IDs/verifiers and
cross-role subject collisions are rejected.

The store writes a mode-0600 same-directory temporary file, flushes and fsyncs it,
atomically replaces the destination, then fsyncs the directory. It only publishes
validated records in memory; after an I/O error it reloads the visible disk state.
Loads tighten permissions to 0600, reject corrupt/unknown schemas with a generic
error, and clear stale in-memory grants before loading. Missing files mean no
principals, not automatic owner claim. Settings and routing configuration survive.
The parent data directory must remain writable and controlled by the server account;
this package does not change deployment ownership. Webhook secret persistence also
uses atomic mode-0600 writes.

`get_store()` caches one store per configured data path; startup reloads the store.
Future in-process provisioning/lifecycle must use that same instance. File edits
are not a supported live-reload/revocation mechanism. A disabled/removed credential
fails new authentication and existing WS message checks; channel transcript sends
also check current credentials. **Immediate idle-socket/media disconnect, pending
rotation, ACKs, revocation transactions and session invalidation belong to #49.**
They are not claimed by the schema's `enabled` field.

## Transport behavior

See [inventory.md](inventory.md) for the complete route/message inventory.
HTTP checks happen before handler side effects and use 401 for missing/invalid
credentials, 403 for a recognized principal denied an operation. A middleware guard
also rejects newly registered handlers unless they explicitly declare an auth
boundary. OPTIONS is public preflight; static assets are public. Unknown `/api/*`
GET paths return 404 rather than the SPA. No credentials are accepted in query strings.

Device sockets authenticate on `hello`, `voice.start` or `realtime.start`; all later
messages and mic frames are bound to that principal and registry socket. Supplied
IDs must match the stored subject. Even a second valid credential cannot switch an
existing socket's identity. An additional connection cannot take over a live device;
it must retry after that socket closes. No owner token substitution for browser
voice clients is available. Unknown operations close the socket with a secret-free
authorization error. Malformed JSON is a protocol error and performs no action.

Realtime offers accept the scoped device token in the existing JSON signaling field
or a Bearer header; conflicting credentials are rejected. Identity authorization
precedes checking the device's armed wake and invoking the media manager. Client
`pc_id` and peer restart requests are rejected, because the existing shared
SmallWebRTC handler otherwise permits selecting another device's peer. New offers
remain available. Peer-handle ownership/re-offer support must be implemented before
those features can be enabled. Established media inherits the authenticated offer's
device identity; no owner/integration offer or separate anonymous media admission
exists. TLS/DTLS/server-trust decisions remain #45's scope.

Channel sockets resolve integration credentials from `authz.json`; legacy bcrypt
channel tokens alone are not accepted. A routing record must exist for the bound
subject. Only the currently selected, current socket may submit the three response
message types, routed to an existing device response listener. An inactive channel
cannot inject responses. Outbound OpenClaw-direct is an internal configured backend,
not an inbound credential mechanism.

## Redaction and audit

Device list/config responses allowlist basic settings and omit arbitrary fields and
button prompt bodies. Webhook responses project ID/name and presence flags only:
no authorization, URL (which can carry userinfo/query secrets), or arbitrary body.
Channel lists never include token hashes or credentials. Approval's reserved
`PairApprovalResult.public_dict()` contains exactly `status` and `device_id`.

Authorization audit entries contain only unauthorized/forbidden, with no submitted
path, ID, token, body, SDP, or exception text. HTTP access logging is disabled in the
server entry point so URL-carried attempted secrets are not recorded by aiohttp.
Announce/channel text and webhook request exceptions are not logged by these
boundaries. This does not promise global content scrubbing for unrelated pipeline
or external proxy logs; trust/owner packages must retain this secret-free auth
logging contract. Do not log raw signaling or owner/enrollment inputs.

## Downstream handoff and validation

- **#47 owner:** implement owner generation/login/session/console claim and env
  precedence; authenticate sessions into an owner principal with session invalidation,
  CSRF/origin/rate-limit checks. Do not map DEVICE_TOKEN to owner. `config.py` still
  requires DEVICE_TOKEN to construct runtime configuration; it grants no access here.
- **#48 enrollment / #51 integration:** verify fresh physical window, matching spoken
  code, request/device/server binding, expiry, attempts, replay, and existing ownership
  before authorizing pairing. `physical_verified` is trusted **server-computed context**,
  never a request boolean. Initiation must also validate device participation. Deliver
  secrets only over the authenticated enrolling-client channel; project approval to
  `PairApprovalResult`. Software browser enrollment is a separate owner-only contract.
- **#49 lifecycle:** build serialized issuance/rotation/revocation on the shared store;
  handle new IDs, bounded overlap, durable ACK, disconnect all transports immediately,
  and restart/failure semantics. Reserved credential permissions are owner-only.
- **#45 trust / client packages:** trusted HTTPS/WSS and firmware download authentication
  remain prerequisites. No changes were made to `docs/auth/`, `prototypes/auth-tls/`,
  web UI, firmware, plugin, TLS or deployment files.
- **#50 UI / #52 migration / #53 E2E:** legacy shared-token UI smoke and its CI health
  probe are intentionally incompatible and must be replaced with enrolled fixtures.
  Existing UI webhook/body editing also needs the redacted projection contract. This
  draft does not claim a usable clean-install UX or hardware end-to-end acceptance.

Tests: `python3 -m pytest -q tests/test_auth.py tests/test_authz_transports.py
 tests/test_channel_server.py` (one command, on one line) for the focused kernel;
`python3 -m pytest -q` for the broader backend suite. Tests cover each principal and
operation, concrete HTTP endpoints, all device message classes, raw binary rejection,
channel response injection, signaling peer-handle bypass, secret projections/logging,
malformed credentials, persistence permissions, restart and atomic-write failure.
Optional real-media tests skip when pipecat/aiortc is absent; mocked signaling tests
exercise authorization without those heavy dependencies. No UI/hardware/TLS success
is inferred from backend tests.
