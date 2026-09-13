# Enrollment and server trust contract v1 (#48)

Status: server backend contract frozen for downstream #49/#50/#51 and firmware
#69. This implementation is stacked on PR55 and PR56. Browser, firmware and
integration UX, credential lifecycle, deployment and physical acceptance are
separate packages. No integration credential is issued by these endpoints.

## Trust and physical participation

**Authenticate HTTPS before sending any credential, enrollment proof or code.**
Reuse the exact `OWNER_HTTPS_ORIGIN` and trusted immediate proxy contract in
[owner-v1.md](owner-v1.md). Discovery/manual URLs only identify candidate servers.
Clients validate certificate chain, hostname and validity using an already
trusted root and trustworthy time. No HTTP fallback, certificate-warning
clickthrough, IP/name exception, learned unauthenticated pin, or TLS bypass.
API, WSS, firmware download and realtime signaling must use consistently trusted
names. A proxy must replace forwarding headers, preserve public Host, restrict
backend access, and suppress bodies, credentials, cookies and query strings in
logs. Direct TLS is also accepted by the existing owner boundary.

The public `server_id` is a persisted random installation identifier, authenticated
**by HTTPS**, not a trust anchor. Clients retain the authenticated origin and
server_id and require explicit setup if either changes. Ordinary leaf certificate
renewal with the same validated name/root does not change the enrollment identity.
Root rollover, initial trusted time/root provisioning and first-install trust
remain #45/firmware obligations. **The TLS provider/DNS/service choice is unresolved
and remains a release blocker.** Nothing here provisions DNS, certificates,
services, bootstrap trust or hardware attestation. See the separate TLS work
package's `docs/auth/tls-server-trust.md` decision record (PR54).

An anonymous `request` means only “someone submitted a public key.” It is **never
physical button proof**. A valid `prove` establishes possession of that Ed25519
private key for this challenge and this authenticated server. It still does not
prove a button press, board identity or genuine firmware. For physical enrollment:

1. Firmware requires a deliberate local long-press while unpaired, distinct from
   ordinary gestures, to open a maximum 300-second window with local LED/audio
   feedback. Network messages must never open/extend that window. Already paired
   firmware refuses ordinary enrollment until a deliberate physical reset/transfer.
2. Firmware generates/retains its own Ed25519 key using its maintained crypto
   library and a secure random source, validates server trust, requests a challenge,
   checks all binding fields and signs `prove` only while that window is open.
3. Firmware speaks the returned eight digits **locally**, before any authorized
   voice session exists. It must not use the server voice pipeline or ask an
   integration to fetch/speak the code. The operator hears that intended physical
   device and submits its exact digits through the trusted owner/integration UX.
4. Both initiation and approval require those matching digits. Control clients
   must explicitly identify the request/device, ask for the locally heard code,
   and confirm the physical window. They must not infer consent from a discovery
   message, fresh request, name, a chat-supplied link or a claimed boolean. Never
   automate approval from untrusted tool output. No approval/list API returns code.
5. Firmware signs redemption only after observing approval, while its original
   local window remains open, and validates the returned device identity. Close
   the window on expiry, cancellation, reset, successful enrollment or restart;
   discard pending challenges on firmware restart. A new attempt needs new local
   participation. Never precompute or export redeem signatures.

The human code match connects the trusted control surface to the intended
key-holding client. `physical_verified=True` in the shared policy means this
server-computed code-confirmation boundary, **not hardware attestation**. Malicious
firmware/software can emulate an unpaired speaker, generate a key and obtain its
own code; the server cannot distinguish it from hardware without an attestation
system. A coerced/tricked human can approve the wrong client. These are explicit
limits, not grounds for labeling anonymous traffic as physical proof.

## State and bounds

`challenge -> ready -> initiated -> approved -> consumed`. Denial or signed client
cancellation can end any nonterminal state. Five bad signatures/codes combined
set `failed`. `expired` is a computed terminal projection; `stale` is also durably retained
when observed (including startup): expiry is
`now >= expires_at`; stale means owner epoch/origin changed or either retained
initiator/approver integration credential is no longer current. No action revives
these states. `prove` is single-use and returns the code only once.

All requests expire 300 seconds after server creation, including time spent
awaiting proof/approval. No polling, retries or approval extends expiry. All POSTs
share a persistent global limit of 60 attempts per rolling 60 seconds, charged
before JSON parsing (including malformed bodies). There are at most 64 retained
requests, including terminal results until their original expiry. New creation
prunes expired rows and never evicts live rows. A terminal result can become 404
after pruning. Up to 1,024 total existing credential records are allowed before
this service refuses another enrollment; this is an admission cap, not deletion
of existing credentials. Other writers must coordinate their own limits.

These global limits deliberately tolerate denial of service rather than trusting
spoofable forwarded IP addresses. They survive process restart. A deployment proxy
may add connection limits. No background timer is required for authorization;
every action checks effective expiry and authority under the shared lock. All
deadlines assume a sane server wall clock; backward clock changes/backups are not
an anti-rollback mechanism.

## HTTP contract

Only `POST /api/enrollment/v1/{action}` exists, including list/status. No GET/HEAD
credential endpoints. Every successful result is JSON with HTTP 200. Requests use
`Content-Type: application/json`, at most 4096 bytes, ten-second body-read deadline,
UTF-8 JSON object, exactly the documented fields, no query string. Duplicate JSON
keys, extra fields, wrong types and noncanonical hex are rejected. JSON field order
and insignificant whitespace are irrelevant. No client `physical_verified`,
`button_pressed`, requested `device_id`, role, credential or TTL fields are accepted.

All actions require the configured trusted HTTPS boundary. Any supplied Origin
must exactly equal the configured origin; duplicate Origins are rejected. Native
clients/integrations may omit Origin. Cookie requests always require exact Origin
and the owner session's `X-CSRF-Token`, enforced by owner middleware. Browser
request creation requires a current owner session. Integration controls use a
single `Authorization: Bearer <integration credential>` header. Owner bearer tokens
are never accepted. Simultaneous cookie/bearer or duplicate Authorization headers
are invalid; client actions reject Authorization headers. No cross-origin CORS
grant is emitted. Responses include `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`. Requests, signatures, codes and tokens are not logged;
audit messages are fixed transition names without client-supplied text or IDs.

| Action | Exact request fields | Authority and success body |
| --- | --- | --- |
| `request` | `kind`, `public_key`, `display_name` | physical: anonymous candidate; browser: owner session. Returns binding object below, no code/credential |
| `prove` | `request_id`, `signature` | Bound client key; challenge only. `{version:1,status:"ready",code,expires_at}` |
| `initiate` | `request_id`, `code` | physical: current owner or integration; browser: current owner. Ready only. `{status:"initiated",device_id}` |
| `approve` | `request_id`, `code` | Same role rules, independently freshly authenticated. Initiated only. `{status:"approved",device_id}` |
| `deny` | `request_id` | Same role rules; any nonterminal state. `{status:"denied",device_id}` |
| `redeem` | `request_id`, `signature` | Bound client key, approved only. `{version:1,status:"consumed",device_id,credential_id,device_token}` |
| `cancel` | `request_id`, `signature` | Bound client key, any nonterminal state. `{status:"cancelled",device_id}` |
| `status` | `request_id`, `signature` | Bound client key; public row below, including terminal status |
| `list` | none (`{}`) | Current owner/integration. `{version:1,requests:[public row,...]}`; integrations cannot see browser requests |

`kind` is exactly `physical` or `browser`. `public_key` is the 32 raw Ed25519 public
key bytes encoded as **64 lowercase hex characters**, no prefix or separators.
`display_name` is 1–64 ASCII characters from `[A-Za-z0-9 _.-]`; it is untrusted,
nonunique presentation data and never identity. `code` is a JSON string of exactly
eight ASCII decimal digits, including leading zeros. `signature` is exactly 128
lowercase hex characters encoding the 64 raw signature bytes. `request_id` is 32
lowercase hex characters. All generated random values use Python `secrets`.

The challenge binding object contains exactly these fields:

| Field | Value |
| --- | --- |
| `version` | integer `1` (not a boolean) |
| `request_id` | fresh 128-bit random ID, lowercase hex |
| `server_id` | persistent 128-bit random installation ID, lowercase hex |
| `origin` | exact configured ASCII HTTPS origin, at most 256 characters |
| `kind` | `physical` or `browser` |
| `public_key` | the submitted canonical raw public key hex |
| `device_id` | `dev_` followed by lowercase SHA-256 hex of the **raw public key bytes** |
| `nonce` | fresh 256-bit random challenge, lowercase hex |
| `expires_at` | integer Unix seconds, creation time floored plus 300 |
| `owner_generation` | current 128-bit owner epoch, lowercase hex (public binding metadata) |
| `display_name` | submitted validated name |

A public row is exactly `{request_id,device_id,kind,display_name,status,expires_at}`.
No code, code digest, public key, signature, owner/session verifier or device token
is on list/status/approval surfaces. Approval returns exactly `PairApprovalResult`'s
`status` and `device_id`. There is no approver credential-disclosure endpoint.

Errors contain only `{error:<fixed code>}`: 400 `invalid_request`/`invalid_proof`,
401 `unauthorized`, 403 `forbidden`, 404 `not_found`, 409 `unavailable` (wrong/stale/
expired/terminal state), `already_owned` or `conflict`, 429 `rate_limited`/`capacity`,
503 `enrollment_unavailable` for persistence/corruption failures. Owner middleware
may return its documented generic `owner_unavailable` before handler execution.
Unsupported HTTP methods do not mutate state. Unknown actions are invalid requests.

## Exact signature and matching-code rules

Use standard **Ed25519 (RFC 8032), pure mode**, via maintained library APIs. The
server uses [`cryptography` Ed25519PublicKey.verify](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).
Do not use Ed25519ph, Ed25519ctx, ECDSA, PEM bytes, a JSON signature envelope, a
prehashed transcript, or custom signing/verification math.

For each client action (`prove`, `redeem`, `cancel`, `status`), sign the ASCII bytes
of this compact JSON **array**, with the exact positional order below:

```text
["vauxr-enrollment",ACTION,1,REQUEST_ID,SERVER_ID,ORIGIN,KIND,PUBLIC_KEY,DEVICE_ID,NONCE,EXPIRES_AT,OWNER_GENERATION,DISPLAY_NAME]
```

Uppercase placeholders are values, not field names. Strings have ordinary JSON
double quotes; integers have decimal digits without exponent/fraction. Separators
are exactly comma and colon with **no whitespace**, no BOM or trailing newline.
Strings use ASCII JSON escaping (`ensure_ascii=True`); accepted binding strings
are ASCII and exclude quote/backslash/control characters in names and hex fields.
The exact configured origin is serialized as JSON, including any necessary JSON
escapes. Do not percent-decode, normalize case, ports or URL components. A client
must compare the returned origin to the TLS-authenticated configured origin and
verify key/kind/name/device_id and the retained server_id before signing.

Reference algorithm, using the named fields from the challenge:

```python
fields = ("version", "request_id", "server_id", "origin", "kind", "public_key",
          "device_id", "nonce", "expires_at", "owner_generation", "display_name")
message = json.dumps(["vauxr-enrollment", action, *[challenge[k] for k in fields]],
                     ensure_ascii=True, separators=(",", ":")).encode("ascii")
signature_hex = private_key.sign(message).hex()
```

Each action has a separate domain in the signed transcript. A `prove` signature
cannot redeem/cancel; another request, key, server, origin, owner epoch, identity,
name, kind, nonce or expiry changes the signed bytes. Status signatures may be
reused for polling; they authorize no transition or disclosure. Transition replay
fails state checks. Unknown/expired requests cannot be resurrected after pruning.

After valid proof, the server samples a uniform eight-digit code once and stores
only `SHA256(transcript(challenge, "code") || 0x0a || ASCII(code))`, lowercase hex.
`"code"` here is a digest domain, not a client signing action. This binds the code
verifier to the complete request/server/key transcript. The code is a bounded
human confirmation secret, not a device credential, PAKE, or replacement for TLS
or signature proof. Its low entropy does not protect it against an attacker who
can read the private server database; database compromise is outside this boundary.
Five online bad code/signature attempts share one durable request counter.

## Issuance, races and recovery

Creation rejects concurrent live requests for the same derived device identity;
duplicate display names are allowed. Known credential subjects, **including disabled
records**, block creation, initiation, approval and redemption. Enrollment never
edits/deletes/rotates a known credential or changes device/routing settings. Clients
cannot choose legacy IDs. Physical reset/transfer needs explicit client-side reset
of existing pairing/key/trust and deliberate new enrollment; a server cannot infer
ownership of a device on another server. Firmware must refuse silent multi-server
pairing. Preserving a legacy device ID or releasing a retained revoked identity
requires an explicit owner lifecycle/migration contract (#49/#52), not this API.
A new key makes a new identity; existing settings are preserved, not reassigned.

Initiator and approver may differ and may each be owner or authorized integration
for physical requests. Their authenticated role/subject/ID/generation are retained.
Every control action resolves the actual owner cookie or integration bearer freshly
**inside** `CredentialStore.transaction()`. Approval and redemption recheck the
request's owner epoch and all retained control authorities. Remove/reissue of the
same integration ID with a different verifier cannot revive a request. Owner
recovery invalidates every pending request, including integration-approved ones.
Changing origin also invalidates pending requests durably at startup, so switching
origin A -> B -> A cannot revive an unexamined approval. Owner logout/session expiry
blocks subsequent control actions; already committed approvals retain the owner
epoch authority until expiry/recovery/denial. They are not revoked by logout alone. Lifecycle writers must invalidate affected
pending enrollments in their revocation transaction before any later re-enable of
the exact credential verifier; foundation generation alone does not provide
revocation history or backup anti-rollback.

Final redemption generates a new 256-bit random `vx_dev_` token and a fresh
credential ID, adds the device-scoped verifier and marks `consumed` in **one atomic
snapshot** under the shared process/thread lock. Recovery/deny/revocation and
redemption have a single serialized order. If issuance wins before recovery, the
credential is already paired and recovery preserves it; if recovery wins, issuance
fails. No credentials are delivered to approvers, retained as plaintext or
redisplayed. A captured list/code alone cannot redeem without the client signature.

Restart preserves pending proofs, counters, approval state and client credentials;
owner cookie sessions still expire on restart. Disconnect does not extend expiry.
A lost response before commit can be retried. After commit, a lost proof response
cannot redisplay its code: sign cancellation and start a new request in a fresh
physical window. A lost redemption response leaves `consumed` and a credential
whose secret may not have reached/durably saved on the client. Status truthfully
reports consumption, **not acknowledged installation or connection**. There is no
retry redisclosure. Explicit owner lifecycle recovery is required; enrollment must
not overwrite that known subject. #49 must define durable-save ACK/rotation/revoke
recovery before release. An I/O error after rename may mean commit occurred despite
503; inspect signed status after storage repair and follow the same rules.

## Browser obligations

`kind:"browser"` is an explicit owner-only software flow. Creation needs the owner
session and CSRF; both control steps require the owner. Integrations cannot create,
initiate, approve, deny or list browser requests. It confers no physical assurance.
The same-origin browser generates its own Ed25519 key, checks the binding, proves,
and uses its transient returned code for the owner-authorized software enrollment.
It must not ask a software user to fake a button press or spoken-code check.

Browser voice transports use only the resulting scoped device credential. Never
substitute the operator token/cookie, share private keys across unrelated clients,
put keys/codes/credentials in URLs/logs, or persist the owner token in localStorage.
#50 must choose and implement safe browser key/credential storage, tab/reload/logout
lifetime and microphone UX with #49; this backend does not claim browser acceptance.
The approver API still returns no credential even when both roles run in one browser.

## Persistence and rollout

Schema 3 extends the shared file to
`{version:3,credentials:[...],owner:{...},enrollment:{version:1,server_id,requests,attempts}}`.
The first enrollment rate-limit/request write migrates schema 1/2 through the
already initialized owner service. Other owner/lifecycle writes preserve enrollment
and vice versa. Namespace validation precedes publishing any loaded grant. Writes
require the existing mode-0600 flock/atomic-rename/fsync transaction; no parallel
file or weaker provisioning API. Use the cached `auth.get_store()` in production;
no awaiting I/O while holding its synchronous transaction. Single server/local
filesystem only; no active-active/shared-filesystem support. Older PR55/56 binaries
cannot read schema 3: rollback requires a consistent pre-upgrade backup and loses
new enrollment/credential state. Do not deploy mixed schema writers.

Tests in `tests/test_enrollment.py` cover signature/request/code/server substitution,
role and TLS/Origin/CSRF rejection, concurrency across threads/processes, recovery
and generation races, persistence bounds, malformed input, restart and pre/post
commit failures. They use synthetic credentials. Real TLS/browser/Voice PE/media
acceptance and the unresolved TLS provider choice remain blockers, not test claims.

An interoperability fixture is committed at
[`tests/fixtures/enrollment-v1-vector.json`](../../tests/fixtures/enrollment-v1-vector.json):
fixed challenge, exact ASCII message, Ed25519 signature and the **public RFC 8032
test seed**. Firmware/browser implementations must reproduce it byte-for-byte.
That intentionally public test key must never be used for real enrollment.

Each persisted `requests[request_id]` row contains exactly the eleven binding
fields plus `state`, `attempts` (integer 0–5), `code_hash`, `initiator` and `approver`.
Actors are null or `{role,subject,credential_id,credential_generation}`; owner uses
its independent epoch as credential_id and an empty credential_generation,
integration uses its authenticated foundation principal. Browser actors must be
owners. `code_hash` is empty only in a fresh challenge; terminal challenges that
never received a code use 64 zeroes as a non-secret sentinel. Request map size,
field sets/types, binding identity hash, actors and state prerequisites are
validated on every load/write. These internals are never control API projections.
