# TLS and server-trust architecture (decision record for #45)

## Status

**Recommendation, not a selected deployment architecture.** Vauxr must require
TLS server authentication before a browser, device, or enrollment client sends
an authorization credential. Certificate-warning clickthrough, `verify=False`,
and hostname/IP exceptions are not supported designs. The companion prototype
in `prototypes/auth-tls/` demonstrates this ordering without opening a port.

## Observed integration surface

Today the device control endpoint is WebSocket on `:8765` and the HTTP/UI API
is `:8080`. The web client derives HTTP from the entered `ws:`/`wss:` endpoint.
The optional realtime path additionally advertises an HTTP offer URL and then
uses WebRTC DTLS-SRTP media. Therefore the HTTPS/WSS origin and advertised
realtime endpoint must be named consistently and be reachable by the client;
TLS for the API/control plane does not remove WebRTC ICE/NAT requirements.

## Provisional shared trust and enrollment contract

1. **Discovery is unauthenticated.** QR, mDNS, or a manually entered endpoint
   may supply a display name and candidate URL only; neither is server identity.
2. **Authentication binds a name to trust.** The client validates a certificate
   chain and hostname against an already trusted public/private root or a
   physical bootstrap trust anchor. It then sends an enrollment request over
   that authenticated channel.
3. **Owner approval and speaker pairing are separate questions.** An owner or
   an authorized OpenClaw integration must approve a requested device pairing
   through an authenticated control surface. Speaker pairing requires both a
   bounded physical pairing window and a matching spoken code. This record does
   not require a physical button on the server or define a custom
   enrollment-credential format. The pairing code is short lived, single use,
   and rate limited; it does not replace TLS server validation.
4. **Device identity is distinct.** The eventual enrollment contract needs a
   device-generated identity and replay protection bound to owner approval and
   verified server identity. Its exact credential/attestation and response
   format belongs to the shared auth contract (#46--#49), not this TLS record.
5. **Reconnect is name based.** DHCP/IP changes do not change the verified
   server name. Devices use a configured name and trust bundle, not a learned
   private IP certificate exception.
6. **Time, renewal, and rollover are first-class.** Firmware needs a trusted
   time strategy before certificate validity is meaningful, plus an updateable
   root bundle. Root rollover is an explicit overlap: distribute the next root
   through the old authenticated channel, accept both temporarily, then remove
   the retiring root only after fleet confirmation. Leaf renewal must preserve
   the validated name and issuer path.

This contract deliberately leaves authorization roles, exact owner/operator
login and token format, device certificate format, and endpoint enforcement to
#46--#49.

## Options

| Option | Browser/phone trust | DNS/control and cost | Privacy/offline behavior | Renewal/outage tradeoff |
|---|---|---|---|---|
| Owner-managed public name + DNS-01 | Normal stock trust for the domain; HTTPS/WSS works without installing a root | Owner controls a domain and DNS API/credentials; domain/DNS provider costs and operational dependency | Local gateway can keep serving LAN traffic during an Internet outage until certificate expiry; issuance/renewal needs DNS control | Automated renewal is practical; DNS provider/registrar loss or expired domain breaks future issuance |
| **Vauxr-managed per-install public name + local TLS termination + DNS-01** | Normal stock trust for a name such as 'install-id.devices.example'; no root installation | Vauxr owns/delegates the parent zone, operates DNS-01 authorization and pays domain/DNS/control-service costs; gateway needs a narrowly scoped issuance flow, not a shared fleet TLS private key | DNS-01 proves name control without exposing the gateway to the public Internet; this is **not** a traffic tunnel. LAN clients still need that public name to resolve to the local gateway (for example through supported local DNS); arbitrary LAN browser trust is not solved by Internet access alone | Vauxr carries issuance, renewal, account recovery, abuse, privacy, and DNS availability responsibilities; the gateway generates and retains its own certificate private key |
| Private local CA + explicit trust provisioning | Stock browser/phone trust only after each client installs/provisions the private root; ESP32 can ship/receive the root bundle | No public DNS is required; owner must protect CA key and distribute/revoke roots | Best local-only/privacy story; arbitrary LAN names/IPs are still not automatically trusted by browsers | Root and client fleet lifecycle becomes product work; mobile/browser policy varies and hardware validation is required |
| Managed tunnel/edge/certificate service | Usually normal browser trust through provider hostname/domain | Continuing provider account, DNS delegation and possible price/egress/privacy dependency | Typically needs outbound Internet; not a LAN-offline substitute | Provider manages issuance but creates service/outage/account dependency |
| Bring-your-own HTTPS reverse proxy | Normal stock trust when the owner's proxy presents a valid certificate for its configured name | Advanced owner supplies and operates its DNS, certificate issuance, proxy, routing, updates, and support boundary | Can preserve LAN-only data paths depending on the deployment, but discovery/name resolution and proxy reachability are the owner's responsibility | Suitable integration/advanced path, not a zero-config consumer default |

### Provisional recommendation

For a consumer-facing browser/phone setup flow, recommend a **Vauxr-managed
per-install public name with DNS-01 certificates terminated locally at the
gateway**, if Vauxr accepts operating the related DNS and certificate-control
service. This avoids asking every consumer to own DNS while retaining ordinary
browser/phone trust and avoids making the certificate key a fleet-shared Vauxr
secret. It is distinct from a tunnel: API/control traffic remains LAN-local.

Proposed **web-first** setup, subject to validation: (1) connect the gateway to
the LAN; (2) use the gateway's local console to display a short-lived,
single-use claim code; (3) in the Vauxr web UI, enter that code to claim the
install and complete owner authentication; (4) after claim, generate and use
the eventual operator-token login defined by the shared auth contract; (5)
open the install's HTTPS name on the same LAN; and (6) pair a speaker only
during a physical pairing window while matching its spoken code, with approval
by the owner or an authorized OpenClaw integration.

The local-console claim proves temporary access to the gateway's setup surface;
it is neither owner authentication nor TLS server authentication. Conversely,
the HTTPS reachability bootstrap (public certificate, install name, and local
DNS resolution) establishes a trusted path to the local gateway but does not
authenticate an owner. These two bootstraps must remain independently designed
and tested. This flow assumes a web UI, not a native Vauxr app.

The implementation must prove that the install name resolves locally, real
browsers retain stock trust, and the chosen local-console exposure cannot be
abused before this can be presented as a supported flow. In particular, the
design has not resolved LAN DNS deployment or clients/routers that apply DNS
rebinding protection to a public install name resolving to a private LAN IP.
The proof must cover representative browser, OS, router, and resolver behavior
and document the supported remediation; no bypass or certificate-warning
clickthrough is an acceptable remediation.

Service/cost responsibility under that default: Vauxr operates/finances the
parent domain, authoritative DNS, DNS-01 authorization service, issuance and
renewal control plane, install-name lifecycle, monitoring, incident response,
privacy disclosures, abuse controls, and recovery/support. The gateway owns
its generated TLS private key and terminates TLS locally. Owner-managed DNS
and a pre-existing HTTPS reverse proxy remain advanced alternatives; private
CA remains an explicitly provisioned local-only alternative.

**Genuine unresolved decision:** whether Vauxr will operate the per-install
name/DNS-01 control service and validate the required local-DNS UX, or require
owner-managed DNS/proxy, or support a private-root local mode (with its
platform-specific provisioning, CA-key custody, recovery, and support cost).
No cloud/DNS service was provisioned for this work.

## Evidence and boundaries

* Let's Encrypt documents DNS-01 validation and its ability to issue wildcard
  certificates: <https://letsencrypt.org/docs/challenge-types/#dns-01-challenge>.
* RFC 8555 defines ACME's DNS challenge and account authorization model:
  <https://www.rfc-editor.org/rfc/rfc8555.html#section-8.4>.
* MDN documents that secure contexts are a prerequisite for web features and
  that `localhost` is a special potentially trustworthy case, not arbitrary
  LAN hostnames/IPs: <https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts>.
* ESP-IDF documents the certificate bundle mechanism used to verify server
  certificates: <https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_crt_bundle.html>.
* ESP-IDF's provisioning guide is relevant for the physical/bootstrap
  channel, but it does not authenticate an arbitrary HTTPS server by itself:
  <https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/provisioning/provisioning.html>.

The prototype proves standard OpenSSL certificate-chain, hostname, expiration,
and old/new-root overlap plus retirement behaviors in this development
environment only. It exercises TLS handshake state and deliberately sends a
synthetic credential only after a successful handshake. On failure paths it
does **not** attempt an application write; MemoryBIO.pending measures BIO
buffering and is not evidence that a peer received or did not receive
plaintext. It does not prove public-CA issuance, DNS ownership, local DNS
resolution or DNS-rebinding-filter behavior, clean browser/phone acceptance,
local-console claim abuse resistance, owner/operator authentication, ESP32
firmware acceptance, trusted-time bootstrap, client root installation, NAT/ICE
operation, or production server configuration. Those are explicit follow-ons
before this architecture can be selected or shipped.
