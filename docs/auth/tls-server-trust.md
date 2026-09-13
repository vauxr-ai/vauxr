# TLS and server-trust architecture (decision record for #45)

## Status: final product decision

**TLS is OPTIONAL. Default self-hosted LAN HTTP/WS must work without a domain,
certificate, reverse proxy, or managed service.** This includes owner setup and
login, authenticated control APIs, enrollment and device WS operation. Plain LAN
mode is a supported default, not a development exception. This decision supersedes
this record's former mandatory-TLS recommendation and provisional managed
per-install DNS-01 service proposal. There is no outstanding provider choice
blocking the default LAN product.

HTTPS/WSS is an explicitly opt-in hardened setup using an owner-operated,
Caddy-style reverse proxy. Once TLS is configured, certificate or connectivity
failure must fail closed: never silently retry HTTP/WS, follow a downgrade
redirect with credentials, learn an unauthenticated trust anchor, or bypass
certificate validation. Returning to LAN mode requires an explicit operator
configuration change, with client reconfiguration and session/trust handling.

This is a decision and downstream implementation contract, **not a claim that
#56/#57 already implement the LAN default**. This revision changes documentation
only; production code and sibling worktrees remain untouched.

## Transport and bootstrap contract

| Concern | Default LAN HTTP/WS | Opt-in HTTPS/WSS |
|---|---|---|
| Setup dependencies | Local address or locally resolved name; no domain ownership, PKI, proxy or service prerequisite | Owner supplies a reachable name/address, valid certificate and client trust, and operates the reverse proxy |
| Server trust | Operator selects the intended local endpoint and trusts the LAN and local setup surface; HTTP supplies no cryptographic server authentication | Validate the full certificate chain to a provisioned trusted root, validity period and configured hostname before sending credentials, codes or proofs |
| Exposure | Traffic, bearer tokens, cookies and enrollment codes can be observed or modified by an on-path LAN attacker | TLS protects the client-to-proxy connection; the proxy and private backend hop are trusted |
| Failure behavior | Auth failures remain failures; LAN reachability never grants authorization | Trust/handshake failures stop the operation without plaintext fallback |

QR, mDNS and manually entered URLs supply candidate endpoints, not authenticated
server identities. In LAN mode, first contact and downloaded browser code depend
on the trusted-LAN assumption. The local console's OS access authorizes issuing a
short-lived claim code; neither that code, a public `server_id`, an Ed25519 device
signature nor a spoken match authenticates the HTTP server or encrypts traffic.
The product accepts this limitation for the default. Do not describe HTTP
bootstrap as MITM-resistant, TOFU pinning as verified trust, or matching codes as
a PAKE. Retain the explicitly selected origin and installation identifier and
require explicit setup when they change; persistence detects changes but cannot
retroactively authenticate the first HTTP contact.

In TLS mode, bootstrap the trust root through an already trusted public/private
root store or a deliberate physical/out-of-band provisioning channel. Downloading
a root from an unauthenticated discovered server does not establish trust.
Devices need trustworthy time for certificate validity checks and an updateable
root bundle. Reconnect validates the configured name despite DHCP changes. Root
rollover distributes the next root through the existing authenticated channel,
allows an explicit old/new overlap, then retires the old root. Leaf renewal keeps
the validated name and trusted issuer path.

All auth protections remain required in **both** modes: generated and persisted
operator credentials (or the authoritative configured `OPERATOR_TOKEN` override),
bounded single-use claim/save flow, owner authentication, scoped integration and
device identities, role/subject/scope enforcement, expiry/revocation and replay
checks, rate limits, Origin/Host checks and CSRF protection for browser sessions.
An owner credential must not become a device voice credential. Credentials must
stay out of URLs, logs and persistent browser token storage.

Physical speaker enrollment still requires a deliberate local pairing window
(maximum 300 seconds), a device-generated key and proof of possession, and the
exact eight-digit code spoken locally by the intended device. Both initiation
and approval require that match plus authorized owner/integration participation.
Network messages cannot open or extend the physical window; discovery and a
client-supplied boolean do not prove physical presence. Redemption requires
approval and a fresh action-bound signature while the original local window is
open. Keep expiry, attempt bounds, single-use transitions and transcript bindings.
This human confirmation is not hardware attestation and cannot repair an
attacker-controlled LAN transport. Browser enrollment retains its separate scoped
identity and approval rules without being labeled physical proof.

## Optional Caddy-style HTTPS path

An owner who wants hardened transport or LAN browser microphone access can:

1. Choose a stable endpoint reachable from the clients. Use an owner-managed name
   with a publicly trusted certificate, or a private CA explicitly provisioned on
   every browser/phone/device. Private CA trust on the proxy host alone does not
   install trust on other clients. No Vauxr-managed domain/service is required.
2. Put a Caddy-style reverse proxy in front of the gateway. Serve the UI and API
   over HTTPS and proxy device WebSocket upgrades over WSS; publish the correct
   external HTTPS realtime signaling URL. Serve the leaf and required intermediate
   certificates so clients can build the full chain to their trusted root, and
   require hostname and validity verification. No warning clickthrough,
   `verify=False`, hostname/IP exception or trust-all mode is supported.
3. Explicitly configure the external origin and trusted immediate proxy peers.
   Preserve the public Host; strip `Forwarded` and replace `X-Forwarded-Proto`
   with exactly one `https` value. Restrict the backend to the proxy through a
   private network or equivalent access control. Arbitrary client forwarding
   headers must never establish secure transport. Do not forward plaintext
   credential submissions; an HTTP redirect cannot undo a credential exposure.
4. Operate certificate renewal, root provisioning/rollover and local resolution.
   Omit credentials, cookies, bodies and query strings from proxy logs. Test
   client trust and fail-closed behavior before relying on the setup.

Caddy supports automated certificate management and local CA issuance; local
trust distribution and deployment configuration remain the owner's responsibility.
See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).
A public-name/private-IP setup may need local DNS and router/resolver validation,
including DNS rebinding filters. These are optional HTTPS deployment concerns,
not prerequisites for LAN mode. TLS on signaling does not resolve WebRTC ICE/NAT
reachability; DTLS-SRTP media does not authenticate an HTTP signaling channel.

## Browser microphone boundary

**Plain LAN browser microphone capture is not guaranteed or expected to work.**
The HTTP control UI and device WS support requirement does not promise browser
voice capture. `getUserMedia` is a secure-context API; serving the page over an
ordinary LAN IP/name using HTTP does not meet that requirement. WSS alone does
not make an HTTP page secure. Loopback/localhost can be treated as trustworthy,
but localhost refers to the browser's own machine, not a remote LAN gateway.
See [Media Capture secure-context interfaces](https://www.w3.org/TR/mediacapture-streams/#navigator-interface-extensions)
and [Secure Contexts trustworthy origins](https://www.w3.org/TR/secure-contexts/#is-origin-trustworthy).

The optional HTTPS path above must serve the page itself with browser-trusted TLS,
use WSS and HTTPS API/signaling URLs, and still obtain browser microphone permission.
UI follow-up must detect `window.isSecureContext` and the availability of
`navigator.mediaDevices?.getUserMedia`, explain the restriction and offer HTTPS
setup guidance while keeping LAN controls usable. Handle denied permission or
missing hardware separately. Never recommend browser security flags or certificate
warning bypass as the supported microphone path.

## Read-only #56/#57 audit and required follow-ups

Audited local sibling snapshots: `vauxr-auth-owner` at `3dae4ac` (#56, owner #47)
and `vauxr-auth-enrollment` at `bdc0dbe` (#57, enrollment #48). Paths below are
relative to those worktrees; shared owner findings also apply to #57's copy.
This audit uses local files, not a remote PR status check. These are required
follow-ups for the downstream auth/UI/firmware integration, not edits made here.

| Observed code/docs | Required downstream change and acceptance |
|---|---|
| #56 `src/owner_auth.py:trusted_origin` accepts only HTTPS; `src/owner_http.py:attach_owner` reads `OWNER_HTTPS_ORIGIN`; `secure_request` rejects missing origin, plain transport and untrusted proxies. `owner_middleware` gates `/api/auth/*` and cookie-bearing requests. | Introduce a mode-aware, exact canonical origin contract supporting the default local HTTP origin without TLS environment configuration. Establish the intended LAN origin without trusting arbitrary Host/forwarding input. Preserve exact Host/Origin, JSON and CSRF checks. Keep existing HTTPS/proxy checks in configured hardened mode; malformed or incomplete TLS configuration must fail closed, not select LAN mode. Cover claim, save, login, session, logout and cookie-authenticated control requests in both modes. |
| #56 `src/owner_http.py:COOKIE` is `__Host-vauxr_owner`; login and logout hard-code `secure=True`. | Keep that Secure, HttpOnly, SameSite=Strict, Path=/, no-Domain cookie in TLS mode. Provide a distinct unprefixed, host-only HTTP session cookie in LAN mode with HttpOnly, SameSite=Strict, Path=/ and the same expiry/CSRF checks. Do not simply remove Secure from a `__Host-` cookie. Select cookie identity by configured mode, reject cross-mode sessions, and invalidate sessions on mode/origin changes; clear the applicable cookie on logout. Prove browser login round-trips on a non-loopback HTTP origin as well as HTTPS. |
| #56 `docs/authz/owner-v1.md` requires HTTPS before claim/token submission and treats the prior #45 decision as provisional; `docs/authz/inventory.md` describes HTTPS-only bootstrap. | Replace the universal prerequisite with the two-mode contract. Preserve local-console authority, one-time claim/save acknowledgement, generated-token persistence, override/recovery behavior and transient browser credential handling. Explain LAN bootstrap trust and exposure accurately; do not make DNS/provider selection a default release blocker. |
| #57 `src/enrollment_http.py:enrollment_endpoint` always calls `secure_request`; `attach_enrollment` inherits the owner origin. `src/enrollment_schema.py:validate_enrollment` requires persisted origins to start with `https://`. | Apply the shared mode-aware transport boundary to every enrollment action, including anonymous challenge/proof and integration bearer approval. Extend strict origin validation and persisted-state handling for the selected HTTP origin. Keep HTTPS states valid, exact signed origin binding, owner-generation checks and stale-request handling on origin/mode change. Coordinate schema compatibility and client transcript fixtures; do not strip the scheme or remove origin from signatures. |
| #57 `src/enrollment.py` binds the configured origin and server_id into challenges; `docs/authz/enrollment-v1.md` calls server_id HTTPS-authenticated and mandates TLS before all proofs/codes. Its introduction, signature rules and final acceptance claims still assume mandatory TLS; `docs/authz/README.md` calls provider choice unresolved. | Update bootstrap and client comparison rules for each mode. In LAN mode server_id is an installation identifier, not authenticated server trust; in TLS mode compare against the verified configured origin. Keep Ed25519 action-separated transcripts, nonce/key/device/server/owner/expiry bindings, eight-digit matching at initiation and approval, physical-window firmware requirements, role/scope checks and revocation-aware redemption. Update the inherited owner docs too. |
| Shared `src/owner_http.py:owner_middleware` passes non-owner requests without cookies onward; it is not a universal bearer/WS TLS gate. `src/server.py:_hello` advertises an `http://` realtime offer. `web-client/src/hooks/useHttpApi.ts:deriveHttpUrl` maps WSS to HTTPS but assumes the backend HTTP port. | Audit HTTP bearer APIs, device/channel WS, enrollment, firmware URLs and realtime signaling together. Allow authorized HTTP/WS in LAN mode; enforce configured TLS at all hardened entry points and restrict backend bypass. Preserve public proxy origin/port/path in advertised and derived URLs; never advertise HTTP from configured TLS or retry plaintext on reconnect/redirect. Keep existing transport authorization and identity checks in both modes. |
| Shared `web-client/src/hooks/useAudio.ts` calls `navigator.mediaDevices.getUserMedia` directly. Current web API code still uses bearer tokens rather than implementing the owner session UI. | Implement the secure-context UX above alongside mode-aware owner sessions and separate scoped browser enrollment. Test missing media APIs, insecure context, permission denial, and HTTPS capture separately from HTTP control functionality. Do not represent existing backend tests as browser acceptance. |

Secure cookies normally cannot be set/sent over ordinary HTTP, and `__Host-`
cookies require Secure, Path=/ and no Domain. Localhost exceptions are not a LAN
session design. See [Set-Cookie attributes and prefixes](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie).

Downstream tests must cover default HTTP setup/login/enrollment and authenticated
WS without PKI/proxy configuration, all existing unauthorized/wrong-scope and
physical-code rejection cases in both modes, session isolation, spoofed proxy
headers, origin/CSRF rejection, persisted origin transitions, and configured TLS
failure without downgrade. Extend `tests/test_owner_auth.py`,
`tests/test_enrollment.py`, `tests/test_authz_transports.py` and relevant web/firmware
tests in their owning packages. Existing tests requiring HTTPS-only bootstrap
must become mode-specific rather than deleting their hardened rejection coverage.
Browser, proxy and hardware acceptance remain downstream work; none was run here.

## Prototype evidence and limits

The companion `prototypes/auth-tls/` exercises **only the opt-in TLS branch** using
OpenSSL and paired MemoryBIOs without opening a socket. It validates hostname,
trusted/untrusted issuer, expiry and old/new-root overlap and retirement. It sends
a synthetic credential only after a successful verified handshake; failure tests
do not attempt application writes. BIO buffer state is not evidence of peer-side
plaintext receipt on failure.

No prototype behavior correction is needed for optional TLS: these checks remain
mandatory when TLS is selected. The fixture uses a directly root-signed leaf; it
does not test a deployed proxy's intermediate-chain delivery. It also does not
prove LAN-mode auth, cookies, browser/phone microphone acceptance, public-CA
issuance, DNS resolution, private-root provisioning, firmware trusted time,
ESP32 acceptance, physical participation, NAT/ICE or production configuration.
Those limits do not reopen the final optional-TLS product decision. This revision
uses no live listeners, services, real credentials, deployment or GitHub actions.
