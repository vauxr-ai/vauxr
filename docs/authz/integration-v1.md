# Integration enrollment contract v1 (#51)

This is the server contract for software integrations such as the OpenClaw plugin.
It extends the existing owner, device enrollment and credential lifecycle services;
it does not add another credential store or a device enrollment protocol. The
[fixture](../../tests/fixtures/integration-v1.json) freezes the versioned fields.
Owner UI (#50), downstream plugin implementation and real client persistence
acceptance remain separate work. Synthetic tests do not establish those claims.

## Protocol choice

[OAuth 2.0 Device Authorization Grant (RFC 8628)](https://www.rfc-editor.org/rfc/rfc8628.html)
provides the useful user-code, separate approval and client-polling pattern. This
contract deliberately is not OAuth wire-compatible: it uses client-generated
request ID/secret and an absolute deadline, strict JSON action bodies, the existing
local owner session, fixed role grants and a separate durable-save ACK before
activation. It defines no OAuth client registration, discovery, verification URI,
token endpoint, refresh token or OAuth error vocabulary. These choices retain the
existing atomic authorization/lifecycle store and bounded replay semantics without
introducing a second authorization framework. Generic OAuth device-flow clients
need an explicit adapter and cannot use these endpoints unchanged.

The current HTTP/WS LAN-default requirement supersedes #51's original TLS-mandatory
acceptance wording. This also deviates from RFC 8628's HTTPS requirement; LAN mode
is not represented as OAuth compliance or cryptographic server authentication.
Opt-in HTTPS/WSS preserves strict certificate validation and no downgrade.

## Request and owner approval

All actions are JSON `POST /api/integrations/v1/{action}`. Each body has exactly the
fields below: unknown/missing fields, duplicate JSON keys, non-object bodies,
Authorization headers and query strings are rejected. Bodies are bounded to 4096
bytes and ten seconds. The response version is integer `1`; it is not a body field.

| Action | Exact body fields | Authority |
| --- | --- | --- |
| request | request_id, request_secret, origin, display_name, expires_at | requesting client |
| status | request_id, request_secret | same client |
| deliver | request_id, request_secret | same client, after approval |
| ack | request_id, request_secret, credential, saved | same client, after durable save |
| cancel | request_id, request_secret | same client, unfinished request |
| list | none (`{}`) | current owner session + CSRF |
| approve | request_id, user_code | current owner session + CSRF |
| deny | request_id | current owner session + CSRF |

The client generates a cryptographically random lowercase 32-hex `request_id` and
64-hex `request_secret` (128 and 256 bits). Preserve them privately for retries.
`origin` is the exact configured server HTTP/HTTPS origin, not the client's origin.
`display_name` matches `[A-Za-z0-9][A-Za-z0-9 ._()-]{0,63}`. `expires_at` is an integer
Unix UTC deadline in the future, at most 300 seconds away. Retrying an identical
request returns its existing state; changing bound metadata conflicts. The deadline
never extends. An owner must already have completed generated/environment setup.

Successful request returns public metadata plus `user_code`. Public metadata is
exactly `version,request_id,server_id,origin,channel_id,display_name,expires_at,state`.
`server_id` is the shared enrollment server ID (32 hex); `channel_id` is
`int_` followed by the request ID. These IDs and the display name are not proof of
server identity. All actions except list return this public projection; list returns
`{"version":1,"requests":[...]}` with only public projections.

The code is eight uppercase hex characters: the first eight characters of SHA-256
of `b"vauxr-integration-code-v1\0" + bytes.fromhex(request_id + request_secret) +
str(expires_at).encode("ascii")`, uppercased. The client displays the code and server
endpoint; the owner checks the intended integration and code, then approves that
request in an authenticated owner surface. Codes are not bearer credentials. Five
incorrect approval attempts durably mark the request failed. Approval is bound to
the original owner generation, origin and deadline. Only the owner can list,
approve or deny integration requests; another integration cannot approve itself.

## Automatic delivery, persistence and failures

1. The client polls status (recommended interval 15 seconds with jitter/backoff).
   `pending` means approval is outstanding; `approved` permits one delivery.
2. The client calls deliver automatically. In one shared store transaction the
   server generates an independent 256-bit `vx_int_` URL-safe token and a random
   32-hex credential ID, stores only its verifier, and commits `delivered` with the
   credential disabled. The reply adds exactly `credential,credential_id,save_required`
   with `save_required:true`. No owner copy/paste or secret-export endpoint exists.
3. The client atomically saves the credential and request binding into protected
   durable storage, commits/flushes it and reads it back. Only then does it send ack
   with the credential in the JSON body and boolean `saved:true`. Client actions
   carry no owner cookie or Authorization header. A string/number in `saved` fails.
4. ACK verifies the saved credential and request secret and atomically records
   `completed` while enabling authentication. Before ACK there are **no grants**.
   The server cannot attest client storage: this is an explicit client obligation.
   A lost ACK reply is safely retryable using the same saved values after restart.
5. Delivery is never redisclosed. `delivered` means the server committed the attempt,
   not that the client received or saved it. If the response or client save is lost,
   do not claim success or keep requesting delivery. Cancel if possible and begin
   a fresh request with fresh ID/secret and explicit owner approval. No automatic
   approval or revival of old credentials follows re-enrollment.

States are `pending,approved,delivered,completed,denied,cancelled,failed,expired,stale,
revoked`. Deny/cancel can retire an unfinished delivered credential; repeat of the
same deny/cancel is idempotent. Completed requests cannot be cancelled or denied:
use owner lifecycle revoke. Unfinished requests expire at the original deadline,
and become stale after owner-generation or configured-origin changes. Any issued
credential is disabled/tombstoned. Late approval, delivery and ACK cannot revive it.
Startup and periodic maintenance persist these transitions across restart. Completed
requests survive their enrollment deadline; lifecycle controls their credentials.
After rotation the enrollment remains completed while a replacement is valid;
after all subject credentials are tombstoned it reports revoked.

Errors are JSON `{"error":"code"}`: 400 `invalid_request`, `invalid_ack`, or
`invalid_code`; 401 `unauthorized`; 403 `forbidden`; 404 `not_found`; 409 `conflict`
or `unavailable`; 429 `capacity` or `rate_limited`; 503 `integration_unavailable`.
The shared owner boundary can also return `owner_unavailable`. Responses use
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`. A timeout/503 may follow
a committed write: query status, never infer rollback. Enrollment, integration and
lifecycle share a durable global 60-POST/60-second admission budget, charged before
body parsing. Back off on 429; polling is not a per-client availability guarantee.

## Exact grants and routing

The credential authenticates role `integration`, subject equal to its channel ID.
There is no requested scope field, owner grant inheritance or generic admin token.

| Operation | Integration authority |
| --- | --- |
| devices.list | GET /api/devices |
| device.announce | POST /api/devices/{id}/announce |
| device.control | POST /api/devices/{id}/command, validated command allowlist |
| firmware.initiate | command `ota`; initiation only, no image publication/upload/read grant |
| pair.initiate, pair.approve | [device enrollment v1](enrollment-v1.md), fresh verified physical pairing only |
| channel.connect | channel.auth using its saved token, bound to its own channel |
| voice.respond | response delta/end/error for an existing listener on the active bound channel |
| device.playback | reserved policy grant; no shipped playback URL endpoint |

Physical pairing requires the existing signed proof/local pairing window and code
confirmation; integrations cannot approve browser enrollment or known-device
recovery, choose a replacement key, or receive a device's credential. No grants
include owner administration, credential create/disclose/rotate/revoke, device
configuration/button mappings, channel configuration/listing, webhook configuration,
server management, firmware publication or device impersonation. See the complete
[route inventory](inventory.md) for transport checks and reserved operations.

Issued channel metadata is projected from the atomic authorization snapshot, with
no bearer copied into channels.json. Enrollment does not automatically activate a
channel. The owner selects it through the existing channel activation route. The
client sends `{"type":"channel.auth","token":"<saved credential>"}` on the
configured channel socket (default `/channel`); the usual ten-second authentication
timeout applies. No integration enrollment WS frames are introduced. Retained
connections recheck current authority; rotation/revoke closes retired authority.

## Rotation and revocation are separate from devices

Use [lifecycle v1](lifecycle-v1.md) with `role:"integration"` and the exact channel
subject. Only the owner initiates rotation/revoke. The integration may poll, receive
its own one-time replacement, durably save it and ACK within the bounded overlap.
That subject-only delivery is not a credential-management grant. Preserve the
lifecycle versioned operation IDs, deadlines and replay/tombstone rules.

Integration revoke invalidates all its current/pending credentials and pending
device approvals and tears down its channel/dependent turns. It does not revoke
device credentials, erase device settings or revoke other integrations. Device
rotation/revoke/recovery remain independent. Lifecycle recover rejects integration
subjects: lost enrollment or expired replacement delivery requires fresh integration
enrollment with owner approval. New enrollment creates a new channel subject and
does not silently replace an old integration; explicitly revoke an obsolete subject.

## Exact origins, CSRF and transport selection

Use the [owner v1 transport configuration](owner-v1.md). Default LAN operation is
HTTP/WS, with `OWNER_HTTP_ORIGIN` set to the exact local HTTP authority (default
`http://localhost:8080`). No domain, certificate or cloud setup is required. HTTP
provides no cryptographic server authentication or confidentiality; the selected
endpoint, LAN and owner setup surface are trusted. The displayed code does not
turn HTTP into authenticated transport.

Every integration enrollment POST requires the selected scheme and exact Host,
including any configured non-default port. Native client requests may omit Origin;
if present it must equal the configured origin exactly, with no duplicate headers.
Owner list/approve/deny require the selected owner cookie, exact Origin and valid
`X-CSRF-Token` even though list is read-only (it is POST). Client actions reject
owner cookies; every action rejects Authorization. No credentialed cross-origin
access or wildcard CORS is enabled on these endpoints. Cookies from the other
transport mode and mixed authentication are rejected by the shared owner boundary.

Opt in with `OWNER_HTTPS_ORIGIN` and use HTTPS/WSS throughout. Invalid TLS settings
fail startup. Clients must validate certificates and hostname/IP SAN, trusting a
configured CA explicitly if needed; never disable verification or silently retry
HTTP/WS after TLS failure. Current listeners use the existing trusted TLS proxy
contract: exact configured Host, immediate peer in `OWNER_TRUSTED_PROXIES`, exactly
one `X-Forwarded-Proto: https`, no `Forwarded`. Direct TLS accepts no forwarding
headers; LAN enrollment rejects all forwarding headers. Untrusted proxy assertions
cannot authorize plaintext. TLS mode also checks this boundary for integration
HTTP bearer operations and the channel WS upgrade. Channel upgrades reject query
strings and duplicate/foreign Origin in both modes; native clients may omit Origin.
LAN channel WS may use the device listener's port; exact owner Host is required
for enrollment and for the TLS channel boundary. Ordinary native bearer HTTP calls
are not owner-cookie CSRF calls; do not mix their authentication modes.

## Persistence and compatibility

Schema **5** adds `integration` to `version,credentials,owner,enrollment,lifecycle`.
The namespace has version 1, bounded requests and the selected active channel ID.
Loaders accept earlier schemas 1–4; every writer preserves the integration namespace
once present. Verifier binding checks reject a credential with the wrong role or
subject. Atomic private snapshots (mode 0600) and the existing process/thread lock
commit metadata and credential transitions together; plaintext request secrets,
approval codes and bearer tokens are never stored or logged by this service. Audit
messages contain only the enrollment state transition, not submitted names, IDs,
codes, tokens or request bodies. Display names are bounded untrusted labels, not
verified software identity; never approve based on the name alone.

At most 64 unissued requests and 1024 retained integration rows are admitted.
Expired unissued rows can be pruned; their absolute deadlines prevent replay.
Issued rows remain for binding/history. Shared lifecycle credential, operation and
permanent tombstone capacity/headroom limits still apply. Do not remove namespaces
or history to regain capacity. Older binaries reject schema 5; mixed writers and
partial rollback are unsupported. Full-store backup rollback is not prevented by
an external monotonic authority. Preserve a consistent private backup and treat
restoration as a security recovery event, as described in lifecycle v1.

Tests cover synthetic request/approval/delivery/save ACK, restart, lost replies,
pre/post-rename failures, expiry, owner/origin changes, concurrency, revoke races,
scoped grants, routing, rotation and both selected transport/CSRF modes. The existing
optional-TLS suite checks trusted/untrusted, expired and wrong-SAN certificates.
Real plugin durable storage, browser UI, physical-device acceptance and media E2E
remain downstream acceptance work. This change performs no deployment or OTA.
