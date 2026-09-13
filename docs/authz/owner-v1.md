> Enrollment #48 extends the shared snapshot to schema 3 and preserves this owner

Current lifecycle extension: [credential lifecycle v1](lifecycle-v1.md) defines
schema 4 preservation, durable revocation tombstones, bounded rotation/save ACK,
and explicit same-key/kind enrollment recovery. Earlier package-scoped statements
below about lifecycle being unimplemented or schema 3 being latest are historical;
this versioned extension supersedes those statements without changing owner auth
or ordinary enrollment v1 response fields.
> namespace. See [enrollment-v1.md](enrollment-v1.md) for migration/rollback and
> pending-enrollment invalidation on owner recovery.

# Owner authentication contract v1 (#47)

This package is stacked on authorization foundation **8451b39 / unmerged PR55**.
It implements backend owner authentication and a local-console CLI. Browser UI
belongs to #50; device/integration enrollment and credential lifecycle remain
#48/#49/#51. This is not a deployable end-to-end auth release by itself.

## Transport prerequisite and configuration

TLS is optional. The default self-hosted home-network setup uses HTTP/WS and
needs no domain, certificates, reverse proxy or managed service. Set one exact
`OWNER_ORIGIN`, for example `http://192.168.1.20:8080`, in both the server and
console environment. No trailing slash, path, userinfo, query or fragment is
accepted; Host (including an explicit port) must match. This explicit origin is
still required for owner/enrollment/lifecycle APIs: never derive it from untrusted
request headers. Without it these APIs fail closed. Startup prints no credentials.
HTTP requests must be direct, with no `Forwarded` or `X-Forwarded-Proto` header.

HTTP/WS provides no transport confidentiality or authenticated server identity;
credentials, cookies, pairing codes and media can be observed or modified by an
on-path LAN attacker. Physical confirmation and signed enrollment are not a PAKE
or a substitute for transport authentication. Use this mode on the operator's
chosen home network. Application authentication, exact Origin/Host, CSRF, scopes,
physical participation, explicit confirmation, save ACKs, rotation, revocation and
recovery rules apply identically in both modes. Network proximity grants no role.

For optional HTTPS/WSS, set `OWNER_ORIGIN=https://voice.example.test` (synthetic
name). `OWNER_HTTPS_ORIGIN` remains a compatible HTTPS-only alias; conflicting
settings fail startup and console setup. Clients must validate the certificate
chain to a pretrusted root, hostname/IP SAN and validity using trustworthy time
before sending credentials. No certificate-warning bypass, disabled verification,
unauthenticated learned pin, or automatic HTTP/WS fallback is allowed. A private
CA is possible only when explicitly provisioned as trusted. Selecting HTTP is an
explicit deployment choice, never a retry after TLS validation fails.

The server entry point exposes plain listeners; it does not provision certificates
or a TLS listener. For HTTPS termination, `OWNER_TRUSTED_PROXIES` lists immediate
proxy IP networks (prefer /32 or /128). The proxy must replace `X-Forwarded-Proto`
with exactly one `https`, preserve public Host, strip `Forwarded`, accept credentials
only over validated HTTPS, and restrict backend access to the proxy using a firewall
or private network. Suppress credentials, cookies, bodies and query strings in logs.
Direct aiohttp TLS is also recognized, without forwarding headers. Arbitrary peers,
forwarding chains, duplicate proto values and plain HTTP fail in HTTPS mode.
A trusted proxy and its private backend are inside that mode's trust boundary.

HTTPS sessions retain `__Host-vauxr_owner; Secure; HttpOnly; SameSite=Strict; Path=/`.
HTTP sessions use the distinct host-only `vauxr_owner; HttpOnly; SameSite=Strict;
Path=/` cookie without Secure so browsers can use it on a LAN IP. Both omit Domain
and retain the same expiry, session-generation and CSRF rules. Only the selected
mode's cookie authenticates. Changing scheme/origin requires explicit client setup
and invalidates pending origin-bound enrollment/lifecycle work; never auto-migrate
credentials to a discovered endpoint.

TLS provider/DNS/service selection is not a LAN lifecycle release blocker. Optional
TLS deployment, browser/device acceptance and downstream client implementation
remain separate work; this contract does not claim end-to-end media acceptance.

## Local console and generated everyday login

Run as the server OS account with the same `DATA_DIR` and deployment environment,
using an interactive private terminal without session recording:

```sh
vauxr-owner claim
# Source checkout alternative: PYTHONPATH=src python3 -m owner_cli claim
```

The command requires a configured HTTP or HTTPS origin and prints a random 192-bit,
5-minute, single-use claim code. It never runs implicitly at startup. OS console
access is the authority: the console is not a remotely accessible setup endpoint.
Protect shell/container access as owner-equivalent. Only this explicit command can
open claim; the first remote visitor has no ownership privilege. Reissuing a claim
code invalidates the previous attempt. Five failed code attempts exhaust a code,
including across restart. A correct claim consumes the code durably before returning.

The claim response contains a server-generated 256-bit operator token and a random
save acknowledgement handle. The operator token is displayed **once**. The UI must
require an explicit “I saved this in my password manager” action before submitting
the acknowledgement. There is no choose-your-own-token field or password account.
The pending token cannot log in until acknowledged. The acknowledgement is itself
sensitive and expires after 5 minutes. No owner session is granted by claim/save;
use the saved token at the separate login endpoint. No plaintext token is persisted
or retrievable from the backend. If the response is lost or save expires, use the
console again (claim while unclaimed, recover for an existing owner).

## Authoritative environment credentials

`vauxr-owner generate-token` outputs a cryptographically generated `vx_op_` token
only on a private interactive terminal. Transfer it securely into the deployment's
secret manager as `OPERATOR_TOKEN`; do not paste it into shell command history,
URLs, logs or repository files. The CLI refuses piped/redirected output and has no
bypass flag. As with any displayed secret, terminal recording must be disabled.

An explicitly present override must have the generator's `vx_op_` + 43 URL-safe
character format; empty, whitespace or malformed values fail startup with a
secret-free error. Format validation cannot prove randomness: generate it using
the command rather than inventing a value. The env value is authoritative on
startup for both new and existing installations. Its verifier replaces the
persisted generated/pending verifier. Unchanged overrides keep the durable owner
generation; changed overrides replace it. All process-local sessions also expire
on every process restart, including when the override is unchanged.

Removing an override and restarting transitions to **recovery**, clears the env
verifier, and cannot resurrect any earlier generated token. Run `recover` to
establish a fresh generated credential. CLI commands do not reconcile env removal:
only server startup does, preventing a console missing the service's environment
from inadvertently treating an env-managed installation as generated. While env
management is active, claim/recover explains that the authoritative environment
must be replaced and the service restarted, or removed/restarted before recovery.
A persisted rotation is never presented as overriding the environment. Restart the
single server after every environment change; live environment mutation and multiple
server processes with conflicting environments are unsupported.

## HTTP endpoints

All routes below require the configured transport boundary. Mutations additionally require
`Origin` exactly equal to the configured owner origin, `Content-Type: application/json`,
a JSON object with exactly the listed fields, and a body no larger than 4096 bytes.
Cross-origin and `Origin: null` requests are rejected. Secrets in URL query strings
are rejected. Auth responses (including errors) use `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`; no credentialed CORS is enabled.

| Method /api/auth/ suffix | Request | Response / semantics |
| --- | --- | --- |
| GET status | none | `{version:1, state, environment_managed}`; states unclaimed/recovery/generated/environment, no secrets or claim/pending handles |
| POST claim | `{code}` | `{version:1, operator_token, save_acknowledgement, expires_in:300, save_required:true}`; one-time response |
| POST save | `{save_acknowledgement, saved:true}` | `{version:1, state:"generated"}`; durable activation, single-use ACK |
| POST login | `{operator_token}` | `{version:1, csrf_token, expires_at}` and owner session cookie |
| GET session | session cookie | `{version:1, csrf_token, expires_at}`; 401 if absent/invalid/revoked/expired |
| POST logout | `{}` plus session cookie and `X-CSRF-Token` | `{version:1, logged_out:true}`; revoke that session and delete cookie |

The mode-specific cookie described above has Max-Age 43200 seconds. Absolute server-side expiry is 12
hours with no sliding refresh. Cookie plaintext exists only in the response/client;
the in-memory session table keys are SHA-256 verifiers. Sessions are bounded to 100
per process, expire on restart, and are checked against the durable generation on
every authenticated HTTP request. Logout revokes one session; recovery/env change
revokes all. Already admitted HTTP operations are not rolled back by later logout.
Cookie sessions authorize owner HTTP operations through the foundation policy.
Operator bearer tokens (including old foundation owner records) are rejected by
HTTP, and owner identity never substitutes for a device/integration voice token.

For any cookie-authenticated unsafe HTTP operation, send both the exact Origin and
`X-CSRF-Token` returned by login/session. Claim/save/login use the strict Origin and
JSON requirement without an existing session. The UI must hold tokens and CSRF
values in transient memory only; never put the operator token in localStorage,
URLs, telemetry, persisted application state, or background credential redisplay.
Browser voice enrollment must obtain a separate scoped identity from #48/#50.

All auth POST attempts share a durable ten-per-60-second budget, including invalid
JSON and credentials (429 `rate_limited`). This deliberately avoids trusting
forwarded client IPs or allowing IP spoofing to evade limits. It survives restart
and bounds memory; a remote client can exhaust the global budget and delay login,
so the deployment proxy may add connection limits. Console recovery starts a fresh
budget. Claim additionally has its persistent five-attempt bound. Wrong credentials,
expired/replayed claims and ACKs return 400 with fixed error codes; boundary/CSRF
failures return 403. Storage/corruption failures return generic 503 without raw
exceptions. No auth request body, credential or submitted identifier is logged.

## Durable store and downstream coordination

`DATA_DIR/authz.json` migrates foundation schema 1 to schema **2** on owner startup:

```
{version: 2, credentials: [foundation Credential records], owner: {version: 1, ...}}
```

The owner namespace stores `mode`, a random `generation`, active `verifier` (only
in generated/environment mode), or a bounded `claim` verifier / `claim_expires` /
`claim_attempts` and optional `pending:{verifier,ack,expires}`. `attempts` contains
at most ten rate-limit timestamps. No raw claim, token, ACK, cookie or CSRF value is
written. Owner metadata is validated before any loaded credentials are published.
Unknown/corrupt schemas fail closed. Schema 2 is not readable by PR55 alone; rollback
requires a consistent pre-upgrade backup and invalidates this owner's sessions.

Use **`auth.get_store()`**, the foundation cached store, for every in-process owner,
enrollment or lifecycle change. Owner transactions use an in-process reentrant lock
and a mode-0600 `authz.lock` advisory file lock shared with the console. Downstream
writers must acquire `with store.transaction():` **before** reading records or
constructing a replacement snapshot, then call `store.replace(...)` inside that
boundary. Do not retain snapshots across transactions, create competing in-process
stores, await network operations under this synchronous lock, edit JSON directly,
or delete/replace the lock file while running. Foundation `replace` is a low-level
snapshot writer; calling it outside this boundary is not a supported live writer.
The lock protocol assumes a local filesystem supporting flock, atomic rename and
fsync; network/shared filesystems and active-active servers are not supported.

Owner updates preserve the entire `credentials` collection. A single atomic,
mode-0600 temporary-write/fsync/rename/directory-fsync commits both namespaces;
there is no two-file credential/owner transaction gap. On I/O failure the store
reloads visible disk state before returning failure; it never retains a divergent
old in-memory grant after rename. No token is returned until its pending verifier
is durably written. If a result is lost around a commit, it is not redisplayed:
inspect status and use explicit console recovery. ACK is single-use, so retry after
a lost ACK response may fail; login with the saved token distinguishes completion.
Device settings and routing config files are never changed by owner recovery.

For downstream #48/#49/#51, `owner_http.session_principal(request)` resolves the
session to `Principal(Role.OWNER, "owner", generation)`; use the same middleware and
foundation operation policy for new HTTP handlers. `owner_middleware` enforces the
transport/Origin/CSRF boundary for all requests carrying the cookie, including new
routes. New handlers still need the foundation declared authorization boundary and
an explicit policy check; adding a route is not authorization. Session generations
are **not credential IDs in the paired-client collection** and must not be sent to
socket credential validators. No enrollment/lifecycle secret belongs in owner
status or pairing approval responses.

## Recovery and validation limits

`vauxr-owner recover` deliberately replaces owner access immediately: old sessions,
active generated verifier, pending claim and pending save are invalidated in one
transaction while paired clients/device settings survive. It then displays a fresh
5-minute claim code. If persistence fails before rename, the previous state remains
authoritative and the command fails. If rename succeeded but fsync reported failure,
the visible committed state is reloaded and the command fails without displaying
credentials. Repeat console recovery to obtain a fresh code after storage is fixed.

Focused validation: `python3 -m pytest -q tests/test_owner_auth.py`. Full backend:
`python3 -m pytest -q`. Tests cover sessions, CSRF, untrusted proxies, atomic failures,
concurrent threads/processes, restart and override removal. They use synthetic
credentials and a synthetic configured proxy boundary. They do not replace the
TLS package's real browser/device or clean-install acceptance work.
