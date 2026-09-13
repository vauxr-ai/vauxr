# Credential lifecycle v1 — issue #49

This freezes the server contract for firmware, browser voice clients and integration
plugins. It is stacked on unmerged PR55/56/57, exact base
`bdc0dbe9edf115e4474d5ae8364e8bf8b8138cc2`. Owner access uses the separate owner
session contract; it never becomes device or integration voice authority.

## Trusted transport and exact HTTP interface

Every request below is POST `/api/lifecycle/v1/{action}`, with a UTF-8 JSON object
and **exactly** the listed fields. Unknown fields/actions, duplicate JSON keys,
noncanonical IDs, duplicate Authorization/Origin headers, query strings and mixed
cookie/bearer authentication fail. Bodies are limited to 4096 bytes and ten seconds.
`operation_id` is a client-generated random 128-bit lowercase hex string (32 digits),
retained by the owner UI before the first POST. `role` is exactly `device` or
`integration`; `subject` is the existing stable device ID or routing-channel ID.
There is no credential-ID/role selection in client polling, delivery or ACK.

Use authenticated HTTPS with the exact configured `OWNER_HTTPS_ORIGIN`, including
server certificate validation, **before sending any credential, proof or cookie**.
The existing owner Host/direct-TLS/trusted-proxy boundary, exact same-origin
Origin and CSRF protections apply unchanged. Owner mutations and status require the
owner session cookie, exact Origin and `X-CSRF-Token`. Native subject clients use
`Authorization: Bearer <their credential>`; they may omit Origin. Browser voice
requests must omit owner cookies (`credentials: "omit"`) and send their separate
device bearer. No CORS grant is emitted. All responses, including errors, carry
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

| Action | Exact request fields | Authority and result |
| --- | --- | --- |
| rotate | `operation_id, role, subject` | Owner only; create queued rotation; public result |
| revoke | `operation_id, role, subject` | Owner only; retire all current/pending credentials for exactly this subject; public revoked result |
| recover | `operation_id, role, subject` | Owner only; enrolled device only; retire existing credentials and authorize fresh proof-bound re-enrollment; public queued result |
| status | `operation_id` | Owner or authenticated matching subject; public result |
| poll | none (`{}`) | Authenticated device/integration; own active rotation only; public result or `{version:1,state:"idle"}` |
| deliver | `operation_id` | Authenticated matching subject only, pending rotation; one-time secret result |
| ack | `operation_id, saved:true` | Authenticate with **replacement** credential for this operation; public acknowledged/completed result |

A public result has exactly these fields:

```json
{"version":1,"operation_id":"11111111111111111111111111111111","role":"device",
 "subject":"speaker","action":"rotate","state":"queued","expires_at":2000086400,
 "overlap_until":0,"credential_id":""}
```

`action` is rotate/revoke/recover. `expires_at` and `overlap_until` are Unix UTC
seconds (JSON numbers, potentially fractional). An empty `credential_id` means no
replacement has been issued. Once issued it is a server-generated 32-digit hex ID.
IDs and deadlines are metadata, never bearer authentication. No public result
contains a verifier, token, key, pairing code, approver credential or secret handle.

Successful `deliver` adds **only** `credential` (a generated 256-bit `vx_dev_` or
`vx_int_` URL-safe token) and `save_required:true` to that public result. It is the
only lifecycle endpoint that returns a secret, only to the matching authenticated
subject. Owners, integrations acting as approvers, other subjects, and owner
bearers cannot obtain it. No new WS frame is defined: all three clients use this
same HTTPS polling interface alongside their existing WSS/media transports.
Clients must not send lifecycle frames to `/ws` or `/channel`; unknown frames remain
denied. This avoids dependency on a particular plugin/device socket implementation.

Fixed errors: 400 `invalid_request` or `invalid_ack`; 401 `unauthorized`; 403
`forbidden`; 404 `not_found`; 409 `conflict`, `unavailable`, or `recovery_unavailable`;
429 `capacity` or `rate_limited`; 503 `lifecycle_unavailable` or
`transport_teardown_unavailable`. Enrollment recovery also retains enrollment's
existing error codes. Treat all 503/timeouts as uncertain outcomes and query status;
never infer that a write did not commit. No submitted secrets/bodies are logged.
Lifecycle shares enrollment's durable global 60-POST/60-second admission budget,
charged before parsing. Use jitter/backoff, honor 429, and poll conservatively
(e.g. once per minute); the shared budget is an explicit availability bound, not a
per-client service guarantee. Repeated unauthorized traffic can exhaust it.

## States, deadlines, persistence and lost responses

1. Owner rotation commits `queued`, with absolute expiry 86400 seconds from creation.
   Offline subjects stay queued. A successful subject poll durably records `pending`:
   the subject observed the operation. This is not a live-connectivity claim; pending
   remains pending across disconnect/restart. Neither state means issued or saved.
2. Subject calls deliver with its current credential. The server generates a fresh
   ID/token, writes its verifier and `delivered` together, then returns the token
   once. `overlap_until = min(expires_at, delivery_time + 300)`. Both old and new
   credentials can authenticate during this bounded window. `delivered` means the
   server committed the one-time delivery attempt, **not** proof the network or
   client received, persisted or acknowledged it.
3. Client atomically writes the replacement and operation ID to its durable secret
   store, flushes/commits, and reads back/verifies its saved record. Keep the old
   slot until the new slot is durable; protect key/token material from logs and
   browser owner UI. Only then send `ack` with JSON boolean `saved:true`, authenticated
   by the replacement. A string/number, old token, another subject, or owner fails.
   The server cannot attest client storage: ACK is a client obligation/assertion.
4. ACK atomically tombstones/disables all predecessor credentials, invalidates their
   pending enrollment approvals, and records `acknowledged`. Old socket/media
   authority is disconnected. A subsequent status/ACK/maintenance pass records
   `completed`. Neither state is possible without the replacement-authenticated
   durable-save ACK. Clients reconnect WSS/realtime/channel sessions using the saved
   replacement; they cannot change a retained socket's authenticated principal.
5. A lost ACK reply is safely retryable with the saved replacement and same ID;
   it returns acknowledged or completed. Owner retries with the same operation ID
   return the same operation's current public state; a different action/identity
   with that ID conflicts. A competing rotation conflicts until the existing
   operation is terminal. Deadlines never extend on retries/restarts.
6. Delivery is deliberately not redisclosed. If its final response is lost before
   the client saves the credential, query status with the old credential while
   overlap remains; seeing delivered is not success. Use explicit owner recovery
   for a device. Do not automatically rotate again or overwrite known enrollment.
   If ACK never commits before the delivery deadline, **both** old and replacement
   credentials expire, require re-pairing, and cannot be revived by late ACK. Before
   any delivery, queued/pending expiry creates no secret and leaves the existing
   credential valid. A lost client save similarly requires recovery. Storage failure
   before server rename leaves the prior state retryable; after rename uncertainty
   reloads the visible committed state and returns no secret.
7. Owner revoke supersedes every queued/pending/delivered operation and recovery
   grant for the subject, disables and tombstones every retained current/pending
   credential in one transaction, and makes affected enrollment actors stale in
   that same snapshot. Identity/settings and disabled records remain. A response
   of revoked means authorization is invalidated; active transport closure is awaited
   before success. Teardown failures produce 503 and are retried by maintenance.

Authentication checks deadlines directly, even before the periodic sweep. A
one-second maintenance pass persists expiry and closes idle expired transports;
restart performs the same reconciliation. Owner epoch or configured origin changes
expire unfinished lifecycle operations without extending deadlines. There is one
supported server process; console and server writers share the existing flock and
thread lock. Do not await network operations while holding the persistence lock.
In-flight external effects already performed cannot be undone. Device WS authority,
channel WS authority, armed realtime wakes, retained media offers and active voice
turns are torn down on revocation; a post-await offer check prevents an in-flight
revoked offer from escaping teardown. Integration revocation aborts its active
channel's dependent turns/media but does not revoke any device credential.

## Explicit known-device recovery

`recover` is an owner decision to retire access immediately and open a **300-second**
re-enrollment grant for an already enrolled device. This covers consumed-but-lost
original enrollment replies, lost replacement delivery, and revoked re-pairing.
It is not an export, silent overwrite, identity transfer or settings reset.

Use the unchanged enrollment v1 request/prove/initiate/approve/redeem protocol with
fresh nonce, request, signatures and code, **the original enrolled Ed25519 key and
original physical/browser kind**. The key and kind are retained independently of
short-lived enrollment requests. The grant is bound to exactly one fresh request
at creation; cancellation/failure/expiry needs a new explicit owner recovery.
Original physical clients must open a fresh local recovery pairing window and
speak/confirm the new code; firmware must explicitly support that user-initiated
recovery window even though ordinary enrollment is unpaired-only. Browser recovery
requires its original key and owner-only browser flow. All recovery initiation and
approval is owner-only, including physical recovery: an integration cannot convert
browser recovery into a physical request or bypass it. Possession of the original
key remains mandatory. This is key continuity, not hardware attestation.

Recovery grant, issuance, consumption and binding are committed under the shared
store lock. Revocation during any recovery stage invalidates the request/grant.
Successful recovery redemption returns the enrollment v1 result fields
`version,status:"consumed",device_id,credential_id,device_token`, plus exactly
`operation_id` and `save_required:true`. This explicitly signaled lifecycle extension
requires the same durable-save/ACK procedure above with `device_token` as the bearer.
The deadline remains the original recovery grant expiry, so begin recovery only
when ready; check lifecycle status for it. No response is redisclosed. If this
response is lost, the owner must start another recovery with a new operation ID.
Original enrollment v1 consumption remains a consumption result, not an ACK; this
package preserves its response contract and supplies explicit lost-response recovery.

Existing schema-3 consumed enrollment rows are migrated into durable bindings on
startup before pruning. A disabled known identity with no retained enrolled key/kind
proof is preserved and returns `recovery_unavailable`. The server will not guess a
physical kind, adopt a different key, claim an arbitrary legacy ID, or reset settings.
Loss of the enrolled private key needs a separately designed explicit identity
migration/transfer; it cannot use this same-identity recovery endpoint.

## Integration interoperability and storage limits

Already provisioned integration subjects use the exact same rotate/poll/deliver/ACK/
status/revoke contract, with `role:"integration"` and their existing channel subject.
No device credential or owner credential changes. Package #51 must issue independently
random integration tokens/IDs in this same store transaction, preserve lifecycle
metadata, reject tombstoned verifiers, and explicitly invalidate any replaced
integration generation and its pending approvals. It must define its own fresh
owner-authorized integration enrollment/re-pair proof. `recover` rejects integrations;
no browser/device-key recovery loophole is an integration enrollment API. After lost
integration delivery/expiry or revoke, re-enroll through #51; do not silently restore
an old token. This package does not invent that enrollment UI/proof protocol.

Store schema **4** contains `version,credentials,owner,enrollment,lifecycle`.
The lifecycle namespace version 1 contains bounded `operations`, permanent verifier
`blocked` tombstones, durable device-key/kind `bindings`, and `recovery` grants.
There are at most 1024 retained operations, bindings and recovery entries, 1024
credential admission records, and 65536 verifier tombstones. No live/terminal
operation or tombstone is silently evicted: idempotency and non-resurrection persist
across restart. Capacity returns 429; bounded v1 does not offer automatic history
compaction or unlimited rotations. Plan an explicit reviewed migration before these
administrative limits, preserving every tombstone and known identity. Do not remove
auth records to evade limits. Settings and channel configuration files are untouched.

A tombstone is independent of credential ID and `enabled`. Restoring an exact old
verifier, even under a new ID, cannot restore authentication or a retained principal
while the lifecycle namespace is retained. Old enrollment actor approvals are
persistently stale in the revocation transaction, not merely lazily checked later.
This is **not full-store backup antirollback**: restoring all of `authz.json` from
before revocation also restores pre-revocation history. There is no external monotonic
counter, hardware attestation or trusted remote ledger. Treat full-store restore as
a security recovery event requiring credential replacement and stopped old sessions.
Older schema-3 binaries reject schema 4; mixed-version writers/partial rollback are
unsupported. Back up consistently and never selectively remove the lifecycle namespace.

## Verification and remaining acceptance

`tests/fixtures/lifecycle-v1.json` freezes public/secret field sets, state sequence,
deadlines and ACK obligations for downstream implementations; tests execute the
fixture against both roles. Tests also inject pre/post-rename persistence failures,
client-save/ACK and final-response loss, restart/offline/expiry, recovery/revoke races,
retained authority, role/identity substitution and configured HTTPS/CSRF failures.
These are synthetic server tests. Real firmware durable storage/physical interaction,
browser storage/tab behavior, plugin persistence, stock browser/device TLS acceptance,
real media E2E and clean-install trust bootstrap remain outstanding. No TLS bypass,
new service/DNS provisioning, deployment or hardware claim is included.
