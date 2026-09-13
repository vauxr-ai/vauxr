# Owner and browser UX v1 (#50)

Package baseline: reviewed lifecycle `5354f60fda0b90f1e4deee37426330dd88b28311`.
Stacked dependencies: PR55 authorization, PR56 owner, PR57 enrollment and PR59
lifecycle. Use the built client served by the configured owner origin. The Vite
cross-origin development server cannot authenticate this UI: the server's exact
Host/Origin/CSRF restrictions intentionally remain in force.

## Setup and everyday use

Open the exact configured HTTP origin (`OWNER_HTTP_ORIGIN`; localhost:8080 by
default). On the private server console with the same DATA_DIR and origin, run
`vauxr-owner claim`. Submit that code in the owner page. Save the generated token
in your password manager and explicitly acknowledge saving it within five minutes.
The display is then discarded. Login is a separate step using the saved token.
Neither the server nor this UI offers later token redisplay. Lost response or
expired save: repeat console claim, or `vauxr-owner recover` for an existing owner.
Recovery preserves paired clients, device settings and routing configuration.

Environment-managed status means OPERATOR_TOKEN is authoritative. Use
`vauxr-owner generate-token` in a private console, update the deployment secret and
restart. Browser setup cannot replace an active override. Removing it and restarting
requires console recovery; it does not restore an old generated token.

Administration uses only same-origin HttpOnly/SameSite=Strict session cookies.
CSRF, claim codes, save handles and operator tokens are held in transient memory;
no operator token is stored in localStorage, sessionStorage, IndexedDB, URLs or
application logs. Auth fetches reject redirects and use no-store. The owner cookie
has the contract's absolute 12-hour expiry; restart/recovery invalidates sessions.
The UI checks sessions once a minute and on every administration request. A 401
hides administration and stops voice; network failure is not reported as logout.

HTTP administration works without browser microphone access or a voice connection.
Existing names, device configuration, telemetry, announcements, playback/volume,
webhooks and active-channel selection remain on their existing APIs. Legacy channel
create/export/rotate forms are replaced by integration enrollment (#51) and
lifecycle controls. No credential-disclosure API is added.

## Pairing and lifecycle

Open the intended speaker's physical pairing window and hear its eight-digit code
locally. Refresh pairing, identify the request by its stable ID, enter the heard
code and confirm physical participation. Initiation and approval are separate
confirmed actions. Names are untrusted and nonunique. The page never receives a
hardware token. Consumed means redemption, not proven installation or connection.
Physical speaker/button/audio behavior requires firmware and actual hardware tests.

Rotate/revoke/recover use exactly lifecycle v1. Operation IDs are retained in this
tab's sessionStorage before POST; they are public metadata, not credentials.
After timeout/503, refresh status and retry the same operation ID. Queued/offline,
pending, delivered, acknowledged, completed, expired and revoked are distinct.
Delivery alone is never completion. Recovery retires access immediately and opens
a five-minute same-key/kind grant. Integrations must re-enroll through #51; device
recovery cannot recover integration access. A known offline subject can be entered
explicitly when device listing lacks it. No attempt is made to infer IDs from names.
Status refresh is deliberate to respect the shared enrollment/lifecycle rate budget.

## Browser identity and storage

Connect explicitly authorizes automatic software enrollment through the current
owner session; no physical-proof claim is made. The browser uses native WebCrypto
Ed25519 and validates the frozen signed binding before proof. Its private CryptoKey
is non-extractable and stored with the exported public key, bound origin/server ID
and derived device ID in same-origin IndexedDB. Only this browser's separate
`vx_dev_` credential enters voice JSON authentication. Owner tokens never do.

The credential is a bearer secret in IndexedDB, readable by same-origin JavaScript;
non-extractability of the signing key does not protect the bearer from XSS, browser
extensions, local profile compromise or a substituted HTTP page. Do not share a
browser profile with untrusted users. Browser profile deletion/storage eviction
loses the key; same-identity recovery then cannot work. The server retains the old
identity/settings for explicit owner retirement; it does not transfer them silently.
No encryption key stored beside the bearer is represented as extra protection.

One exclusive Web Lock permits one active voice tab per origin/profile. Other tabs
can administer but cannot concurrently enroll/rotate/use that browser identity.
Reload closes voice, retains the identity and requires Connect again. Closing a tab
retains its credential; it is not a server-side revoke. No unattended reconnect is
started. Browsers without Web Locks, WebCrypto Ed25519 or IndexedDB fail browser
voice clearly while HTTP administration remains usable.

Rotation polls conservatively (60–65 seconds), delivers once, atomically commits the
replacement and operation ID in IndexedDB with strict durability, reads it back,
then ACKs using the replacement bearer with `credentials: omit`. Lost ACK replies
retry from the saved operation. Socket authentication is re-established after ACK.
Lost delivery or revoked access requires explicit recovery with the original saved
key; no automatic re-pair grants access after revocation. Browser storage commit is
a browser/OS guarantee, not physical power-loss proof.

Logout broadcasts stop, waits for this profile's in-flight voice/storage work,
revokes its scoped identity, clears its bearer and pending ACK, then revokes the
owner session and broadcasts logout. It retains the key for explicit recovery.
If revocation or logout fails, the page reports failure and retains owner access
for retry rather than claiming logout succeeded. Recovery/logout in another tab
stops capture/playback and closes sockets. Server session expiry stops voice on the
next check but retains the device credential, because owner expiry/recovery does
not itself revoke paired clients under the contract.

Native WebSocket automatically attaches applicable HttpOnly cookies to its HTTP
upgrade (cookie ports are not isolated). JavaScript cannot suppress that browser
behavior. The voice server does not use those cookies as voice authority: only the
scoped device token in `hello`/`voice.start` authenticates it. Browser lifecycle
bearer HTTP requests explicitly omit owner cookies, as required by lifecycle v1.

Microphone capture is requested on Talk. Permission failure stops capture, closes
its audio context and sends no voice.start. Disconnect/logout cancels capture and
playback, including a permission prompt that resolves late. Physical microphone,
speaker audio and actual STT/TTS inference are separate acceptance work.

## Transport and integration

LAN HTTP/WS is supported and unencrypted. An on-path attacker can intercept/modify
codes, bearer tokens, cookies and page code. Pairing signatures do not encrypt it.
A non-loopback HTTP browser blocks microphone capture; administration still works.
Use localhost or optionally trusted HTTPS/WSS for browser microphone capture.

The voice URL must use this page's exact authority (hostname and port), /ws path and matching WS/WSS
scheme. The aiohttp application exposes /ws on its HTTP listener too. Using the
separate device port with an ambient owner cookie fails exact Host validation;
the browser therefore uses the owner origin's listener. An optional TLS proxy must
forward /ws upgrades on that same origin. Userinfo/query/fragment
are rejected. HTTPS pages never connect WS, retry HTTP or bypass certificate errors.
The browser validates TLS chain/name/validity using its trust store. No managed DNS,
certificate provisioning or warning-clickthrough flow is supplied. See owner-v1
for exact reverse-proxy configuration. This package uses WS voice and does not
advertise WebRTC capabilities or consume plaintext realtime offer URLs.

PR58 was inspected read-only at speech branch dd93663 and deployment/GitHub head
ce45599948a604d0024aca4fa80b119b7a0ea0bf. It adds SpeechSettings inside DevicesPanel
and SettingsPanel. Preserve those additions when integrating. SpeechSettings' direct
bearer fetch must become ownerFetch(path, init), with no inferred port or bearer.
Its GET/PATCH server routes also need explicit operation inventory/policy integration
with PR55 before release; do not relax auth middleware to accommodate them.
This package neither imports the speech server implementation nor edits/deploys the
speech worktrees. #51 owns integration enrollment and its approval UI contract;
this package manages existing integrations via the frozen lifecycle API.


## Verification of this package

`npm --prefix web-client run build` and `npm --prefix web-client test -- --run`
pass (121 tests). The unchanged backend baseline passes 929 tests with two optional
media skips. Run `cd e2e && npx playwright test auth-browser.spec.ts`: three real
Chromium scenarios start isolated aiohttp servers on ports 18080/18765, with a
throwaway DATA_DIR under this worktree. They cover owner claim/save/login/recovery,
CSRF/Origin denial, synthetic signed physical pairing, scoped browser identity,
non-extractable persisted key, device-admin denial, microphone permission denial,
one active voice tab, offline rotation/save/ACK, revocation/recovery, reload,
cross-tab logout and credential cleanup. A non-loopback LAN test proves HTTP admin
with a blocked browser microphone; a separate HTTPS/WSS test rejects an untrusted
certificate without bypass. The latter is rejection evidence, not deployment of
trusted HTTPS or successful browser media over TLS. Tests disable tracing/video/
screenshots to avoid recording generated secrets. Physical speaker/microphone,
real STT/TTS, firmware durable storage, browser/OS power-loss and positive deployed
trusted-TLS acceptance remain untested. No release/deployment/OTA is implied.
