# Native HTTPS in one Vauxr container

Vauxr serves its existing app (UI, API, `/ws` and `/channel`) on an additional
HTTPS/WSS listener. No reverse proxy or sidecar is needed. Native TLS is disabled
by default. HTTP port 8080 and device WS port 8765 remain unchanged; HTTPS defaults
to 8443. Firewall compatibility ports appropriately for your LAN. Enabling HTTPS
does not upgrade existing physical devices to WSS.

## Bring your own certificate

Set these variables in the container (Compose passes them through):

```dotenv
HTTPS_ENABLED=1
HTTPS_PORT=8443
HTTPS_CERT_FILE=/data/tls/fullchain.pem
HTTPS_KEY_FILE=/data/tls/privkey.pem
OWNER_HTTPS_ORIGIN=https://voice.example.com:8443
```

Mount a PEM full chain and its unencrypted key at those paths, readable by Vauxr's
uid 100, gid 101. Restrict key permissions to that user. With the supplied Compose
data mount, these paths are `./data/tls/` on the host. Other certificate locations
need a read-only directory mount. Mount the directory, rather than individual
files, so atomic replacements remain visible. The owner hostname must resolve to
this server. Browser voice uses WSS on the same origin. Clients must verify issuer
trust, hostname and dates, with correct time; private CAs require explicit client
trust provisioning. Never bypass verification.

Unset `OWNER_HTTP_ORIGIN` and `OWNER_TRUSTED_PROXIES` for native mode. An explicit
canonical `OWNER_HTTPS_ORIGIN` is required and trusted proxy configuration is
rejected. The origin is the exact externally reachable authority, including its
non-default port; port mapping can differ from internal `HTTPS_PORT`. Existing
owner console setup still applies. HTTPS and legacy listener ports must differ.

TLS 1.2 or newer is required, with forward-secret AEAD ciphers for TLS 1.2 and
OpenSSL's TLS 1.3 defaults. Compression and session tickets are disabled. Startup
validates certificate dates, owner SAN and the key pair before opening listeners.
Invalid or incomplete TLS configuration fails startup, never selecting HTTP owner
access. Clients remain responsible for trusted-chain verification.

Files are checked every 30 seconds. Replace the full chain and key to renew; a
coherent pair is loaded into a fresh context before publication. Partial writes,
wrong keys, bad names and expired certificates leave the last valid context in
use. Existing TLS connections, including voice WebSockets, survive replacement.
New handshakes stop if the retained chain expires; existing connections can finish.
Watch `vauxr.tls` errors and monitor certificate expiry externally.

## Moving an existing installation to HTTPS

Changing the owner origin invalidates existing owner sessions: sign in again at
the HTTPS URL. Existing physical-device credentials and the legacy `/ws` listener
remain available, but integration HTTP APIs and `/channel` require the configured
HTTPS origin once HTTPS owner mode is selected.

Update OpenClaw's Vauxr URL to the HTTPS origin and enable `strictTls`. Its private
credential store is bound to the server origin, so switching origins requires a
new Connect OpenClaw approval; do not rewrite stored credential bindings. Reload
or restart OpenClaw as required by the installed plugin, approve the new request,
and select the integration as the active route before testing voice.

Update any custom health check that calls `/api/auth/status` to use the new HTTPS
origin with normal certificate verification. An old HTTP owner-status probe will
correctly receive a denial and must not be used to judge the HTTPS service.

## Automatic Let's Encrypt with Route53

The Docker image installs the optional `acme` Python extra (`certbot` and
`certbot-dns-route53`). Source installations can install `.[acme]`. Installation
does not enable automation. Vauxr delegates ACME and DNS changes to Certbot.

```dotenv
HTTPS_ENABLED=1
HTTPS_PORT=8443
OWNER_HTTPS_ORIGIN=https://voice.example.com:8443
ACME_ROUTE53_ENABLED=1
ACME_DOMAIN=voice.example.com
ACME_EMAIL=operator@example.com
ACME_ACCEPT_TOS=1
ACME_STAGING=1
AWS_SHARED_CREDENTIALS_FILE=/run/secrets/route53
```

Supply a real email and explicitly accept Let's Encrypt's terms. One lowercase
DNS hostname must match the owner origin; this wrapper does not support wildcards,
IP addresses or multiple names. Leave manual certificate/key variables unset.
The public authoritative zone must be in Route53. DNS-01 requires outbound AWS
and ACME access but no inbound port 80. Vauxr itself may remain on a private network.

Use an AWS role through the standard SDK credential chain, or a protected
credentials file mounted read-only. For example, a Compose override can add
`./secrets/route53:/run/secrets/route53:ro` to Vauxr's volumes. Make it readable by
only the container user. `AWS_PROFILE` is also passed through. Do not put credentials
in command arguments, source control or application logs. Certbot stdout/stderr
are suppressed; detailed Certbot logs stay under its protected state directory
and should be treated as sensitive.

Staging contacts the test CA and **still changes DNS when you run the service**.
Its certificates are not browser-trusted. After verifying automation, deliberately
set `ACME_STAGING=0` for production. Accounts and lineages are separated under
`DATA_DIR/acme/staging` and `DATA_DIR/acme/production`, each containing `config`,
`work` and `logs`. Persist/protect all of `DATA_DIR`, including symlink targets.
Restart to change mode. Do not delete state to force issuance; observe CA rate limits.

First startup waits for Certbot and validates the result before opening any
listener; issuance failure exits startup. Valid persisted certificates allow startup
during CA outages. One worker checks renewal at startup and every 12 hours using
`certonly --keep-until-expiring`; Certbot decides when renewal is due. Calls use
argument lists without a shell, have a five-minute timeout, and suppress output.
Failures retain the active context and retry at the next interval. A process lock
prevents duplicate automation workers sharing state; Certbot also has its own
locks. Do not add another scheduler against the same state. SIGTERM/SIGINT stop
the worker, kill/reap an active child and clean up listeners. Hard interruption
may leave challenge TXT records requiring operator review. No failure enables
plaintext owner login. After successful startup, compatibility listeners remain
available with their existing authorization rules.

### IAM constraints

The [Route53 plugin](https://certbot-dns-route53.readthedocs.io/en/stable/) needs
`route53:ListHostedZones`, `route53:GetChange` and `route53:ChangeResourceRecordSets`.
Allow listing with `Resource: "*"`; scope polling to `arn:aws:route53:::change/*`.
Grant record changes only on your hosted-zone ARN, with the
[AWS Route53 conditions](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/specifying-conditions-route53.html)
below. Substitute the exact zone and challenge name:

```json
{
  "Effect": "Allow",
  "Action": "route53:ChangeResourceRecordSets",
  "Resource": "arn:aws:route53:::hostedzone/YOUR_ZONE_ID",
  "Condition": {
    "ForAllValues:StringEquals": {
      "route53:ChangeResourceRecordSetsNormalizedRecordNames": ["_acme-challenge.voice.example.com"],
      "route53:ChangeResourceRecordSetsRecordTypes": ["TXT"],
      "route53:ChangeResourceRecordSetsActions": ["UPSERT", "DELETE"]
    }
  }
}
```

Add listing/polling permissions in separate statements. Use a dedicated role
without broader DNS grants, which could undermine these conditions. Record names
must be lowercase without trailing dots. DNS credentials can authorize certificate
issuance: protect them as private keys. Review the policy for your account before
enabling automation. No certificate issuance or DNS changes are needed to run the
local test suite; it uses generated certificates and mocked Certbot operations.
