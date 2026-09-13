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
3. **Physical bootstrap is intentional.** A local owner action (for example a
   one-time code or QR shown while holding a server-side setup control) binds
   the request to the server and owner approval. The code is short lived,
   single use, rate limited, and does not itself replace TLS server validation.
4. **Device identity is distinct.** The enrollment request contains a freshly
   generated device public key/attestation identifier and nonce. Server records
   owner approval + request nonce + device public key + server identity. A
   signed enrollment response is addressed to that device key, not to the
   approving browser; it cannot be replayed for another key or after expiry.
5. **Reconnect is name based.** DHCP/IP changes do not change the verified
   server name. Devices use a configured name and trust bundle, not a learned
   private IP certificate exception.
6. **Time, renewal, and rollover are first-class.** Firmware needs a trusted
   time strategy before certificate validity is meaningful, plus an updateable
   root bundle. Root rollover is an explicit overlap: distribute the next root
   through the old authenticated channel, accept both temporarily, then remove
   the retiring root only after fleet confirmation. Leaf renewal must preserve
   the validated name and issuer path.

This contract deliberately leaves authorization roles, owner login, device
certificate format, and endpoint enforcement to #46--#49.

## Options

| Option | Browser/phone trust | DNS/control and cost | Privacy/offline behavior | Renewal/outage tradeoff |
|---|---|---|---|---|
| Public CA + owned domain, DNS-01 | Normal stock trust for the domain; HTTPS/WSS works without installing a root | Owner controls a domain and DNS API/credentials; domain/DNS provider costs and operational dependency | Local gateway can keep serving LAN traffic during an Internet outage until certificate expiry; issuance/renewal needs DNS control | Automated renewal is practical; DNS provider/registrar loss or expired domain breaks future issuance |
| Private local CA + explicit trust provisioning | Stock browser/phone trust only after each client installs/provisions the private root; ESP32 can ship/receive the root bundle | No public DNS is required; owner must protect CA key and distribute/revoke roots | Best local-only/privacy story; arbitrary LAN names/IPs are still not automatically trusted by browsers | Root and client fleet lifecycle becomes product work; mobile/browser policy varies and hardware validation is required |
| Managed tunnel/edge/certificate service | Usually normal browser trust through provider hostname/domain | Continuing provider account, DNS delegation and possible price/egress/privacy dependency | Typically needs outbound Internet; not a LAN-offline substitute | Provider manages issuance but creates service/outage/account dependency |

### Provisional recommendation

For a consumer-facing browser/phone setup flow, prefer **an owner-controlled
domain with DNS-01 public certificates** if the product decision accepts domain
and DNS operations. It is the only option above that gives ordinary clients
standard trust without installing a root. Keep a private-CA local-only mode as
a separately validated advanced/offline path, not as an implicit fallback.

**Genuine unresolved decision:** whether Vauxr will require/provision an
owner-controlled domain + DNS automation, or will support a private-root local
mode (and bear platform-specific provisioning, CA-key custody, recovery, and
support cost). No cloud/DNS service was provisioned for this work.

## Evidence and boundaries

* Let's Encrypt documents DNS-01 validation and its ability to issue wildcard
  certificates: <https://letsencrypt.org/docs/challenge-types/#dns-01-challenge>.
* MDN documents that secure contexts are a prerequisite for web features and
  that `localhost` is a special potentially trustworthy case, not arbitrary
  LAN hostnames/IPs: <https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts>.
* ESP-IDF documents the certificate bundle mechanism used to verify server
  certificates: <https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_crt_bundle.html>.
* ESP-IDF's provisioning guide is relevant for the physical/bootstrap
  channel, but it does not authenticate an arbitrary HTTPS server by itself:
  <https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/provisioning/provisioning.html>.

The prototype proves standard OpenSSL certificate-chain, hostname, expiration,
and overlap-root behaviors in this development environment only. It does not
prove public-CA issuance, DNS ownership, clean browser/phone acceptance,
ESP32 firmware acceptance, trusted-time bootstrap, client root installation,
NAT/ICE operation, or production server configuration. Those are explicit
follow-ons before this architecture can be selected or shipped.
