# Install and migrate scoped authentication (#52)

**Breaking upgrade: there is no shared-token compatibility window.** `DEVICE_TOKEN`
and old `vx_ch_` channel tokens grant no access in this server. Updating only the
server disconnects old clients. An operator token is owner login authority, never
a replacement token to put in firmware, plugin configuration or an HTTP bearer.

This is a bounded documentation/rehearsal package, stacked on **unmerged
[PR62](https://github.com/vauxr-ai/vauxr/pull/62)**. It is not a release or permission
to deploy. Read the gates below before scheduling an upgrade. The commands are
for an operator's separately authorized installation; the included rehearsal
touches only fresh disposable data.

## Exact compatibility and release gates

These are reviewed source pins as of 2026-09-13, not published compatible releases.
Do not substitute `latest`, a matching package version, or an older store build.

| Component | Concrete baseline | Evidence and limit |
| --- | --- | --- |
| Server and built web client | `8edb3e0e3cfc71a7b09ff1e24187d7721e05f08d` (PR62); package `2.0.0a0` | Combined owner/browser/integration implementation; schema 1–5 reader, schema 5 once integration state is written. Build the UI from the same checkout. |
| Server ancestors | PR60 `b8932451aa8edc9ada97692c09dee675af28754c`, PR61 `16968a73b7610c917a9922d94d8c7ef187f7dda3`; merged PR58 `8ffc9110f0fefcf285b822e7829dcb345ba6b33f` | PR60/61 remain unmerged dependencies of PR62. Earlier owner/enrollment/lifecycle contracts are incorporated; their old package status paragraphs are historical. |
| OpenClaw plugin | [PR37](https://github.com/vauxr-ai/vauxr-openclaw/pull/37), `69794b3a4a9f6859f56c0deba9fc017178639ac1` | Unmerged. Contract tests pinned to PR61, whose contracts/fixtures PR62 preserves. Actual PR37 + PR62 + browser + voice acceptance remains outstanding. |
| OpenClaw SDK | **2026.9.3** on POSIX | The plugin's public private-file SDK adapter was verified at this version. Later versions require revalidation; Windows fails closed in this adapter. |
| Voice PE firmware | Separate firmware #69 candidate against PR61 enrollment/lifecycle v1 | **No available validated artifact or release pin.** Isolated ESP-IDF 5.5.2 compile/link, dependencies, map/image fit and physical acceptance are still missing. Portable tests are not firmware build evidence. Other boards are not covered. |

Before a physical rollout, obtain the reviewed firmware commit, target/config,
artifact SHA-256 and supported installation/recovery instructions, plus successful
isolated build and hardware evidence. The candidate requires roughly 300 KiB of
embedded digit audio; image fit is unverified. Its blocking HTTP operations reject
late results but do not prove a ten-second whole-exchange return bound. Actual
slow-header/body timing and resource release remain an acceptance gap.

[Issue #53](https://github.com/vauxr-ai/vauxr/issues/53) still needs physical
button/spoken-code pairing through both owner and actual OpenClaw approval, voice,
two-device isolation, power loss around save/ACK, reconnect/rotation/revoke and
optional trusted TLS/renewal evidence. See [combined evidence](combined-53.md).
Do not migrate a working speaker installation while its compatible firmware gate
is unresolved. Home Assistant and Matter are separate follow-ups, not these gates.

## Clean server installation

Requirements: Python 3.12 with pip (and venv for the example), Node 22/npm to build
the UI, writable local POSIX storage supporting flock/rename/fsync, and one server
process. Downloads of source, Python/npm dependencies, container images and speech
models generally need internet access during preparation. Cache/pin these before
the outage. LAN auth itself needs no internet, DNS service, domain or certificates.
An external OpenClaw/LLM or speech service can introduce its own ongoing dependency.

For a source installation, use a new directory and a dedicated service account.
The example is loopback administration; replace the origin with the intended fixed
LAN address for remote administration. Run from the repository root:

```sh
git clone https://github.com/vauxr-ai/vauxr.git vauxr-auth
cd vauxr-auth
git fetch origin refs/pull/62/head
git checkout --detach 8edb3e0e3cfc71a7b09ff1e24187d7721e05f08d
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
npm ci --prefix web-client
npm --prefix web-client run build
umask 077
mkdir data
export DATA_DIR="$PWD/data"
unset OPERATOR_TOKEN OWNER_HTTPS_ORIGIN OWNER_TRUSTED_PROXIES
export OWNER_HTTP_ORIGIN=http://localhost:8080
export REALTIME_ENABLED=0
export STT_URL=tcp://127.0.0.1:10300
export TTS_URL=tcp://127.0.0.1:10200
python3 -m server
```

Start with `OPERATOR_TOKEN`, `OWNER_HTTPS_ORIGIN` and `OWNER_TRUSTED_PROXIES`
**absent**, not empty; unset any inherited values before this generated-owner
example. The server does not load `.env` by itself. Put the selected settings into
your actual service environment. On the supplied development VM without venv,
the documented alternative is `python3 -m pip install --break-system-packages .`;
use the dedicated environment when available.

The control plane runs without Whisper/Piper, but voice needs reachable Wyoming
STT/TTS and a connected/selected backend. Source defaults otherwise use Docker DNS
names, hence the explicit loopback URLs above. Keep realtime off for this walkthrough;
it separately needs `.[realtime]` (pinned Pipecat 1.9.0), `REALTIME_ENABLED=1`,
`REALTIME_HOST`, ICE networking and client/media acceptance. Preserve existing
[speech settings](../speech-settings.md) during auth upgrades.

Open the exact selected origin. In a second **private interactive terminal**, as
the same OS account, with the same DATA_DIR and service environment:

```sh
. .venv/bin/activate
export DATA_DIR="$PWD/data"
unset OPERATOR_TOKEN OWNER_HTTPS_ORIGIN OWNER_TRUSTED_PROXIES
export OWNER_HTTP_ORIGIN=http://localhost:8080
vauxr-owner claim
```

Enter the five-minute console code into the owner page. Save the once-displayed
generated operator token in a password manager and explicitly acknowledge saving
within five minutes. Then log in separately using that saved token. Claim/save
does not create a session. Startup prints no credentials, the first visitor cannot
claim ownership, and the server stores only a verifier. There is no token redisplay
or choose-your-own password. Do not record the terminal, redirect the CLI, or capture
the token in screenshots. The source CLI alternative is
`PYTHONPATH=src python3 -m owner_cli claim`.

### Compose deployment caveat and override

The checked-in `docker-compose.yml` does **not** pass owner configuration from
`.env` into Vauxr. It also retains a legacy DEVICE_TOKEN entry (no authority),
an outbound `SSL_CERT_FILE=/certs/root.crt` mount, separate firmware/recordings
mounts and Whisper/Piper health dependencies. Merely editing `.env` is insufficient.
For a separately prepared Compose installation, create `compose.auth.yaml`:

```yaml
services:
  vauxr:
    environment:
      OWNER_HTTP_ORIGIN: http://192.168.1.20:8080
```

Use your exact address. Ensure the existing root.crt mount is a real intended CA
file for outbound TLS, not a directory created for a missing file. It neither
serves HTTPS nor installs browser/firmware trust. If you do not use that outbound
CA, prepare a reviewed deployment configuration removing both its environment
setting and mount. This documentation does not change the base Compose file.
Prepare all bind mounts on the selected Docker daemon host. For **fresh** data:

```sh
mkdir -p data/piper data/whisper
docker compose -f docker-compose.yml -f compose.auth.yaml build vauxr
docker compose -f docker-compose.yml -f compose.auth.yaml run --rm --no-deps --user 0 vauxr \
  sh -c 'chown 100:101 /data && chmod 700 /data'
docker compose -f docker-compose.yml -f compose.auth.yaml up -d
docker compose -f docker-compose.yml -f compose.auth.yaml exec vauxr vauxr-owner claim
```

The container runs UID 100/GID 101; perform ownership preparation through the
selected daemon, especially with rootless Docker. Existing restored files also
need the service's ownership and private modes; the command above only prepares
the directory. Keep the same Compose files for every subsequent command. Never
use `down -v` for migration. Environment changes require container recreation
(`up -d --force-recreate vauxr`); `restart` alone retains the old container env.
The Docker build/volume procedure has not been executed by this docs package.

## HTTP/WS and optional strict TLS

| Client | LAN default example | Optional TLS requirement |
| --- | --- | --- |
| Owner/admin browser | `http://192.168.1.20:8080` | Exact `OWNER_HTTPS_ORIGIN`, browser-trusted certificate before login |
| Browser voice | Same page authority, `ws://192.168.1.20:8080/ws` | Same authority `/ws` via WSS; proxy must forward upgrades |
| Native device | `ws://192.168.1.20:8765/ws`, auth origin `http://192.168.1.20:8080` | WSS `/ws` at the owner authority, matching HTTPS auth origin and trusted roots/time |
| Plugin | HTTP base `http://192.168.1.20:8080`, socket base `http://192.168.1.20:8765` (`/channel`) | HTTPS/WSS, same hostname; use owner authority for TLS channel boundary |

LAN HTTP/WS is unencrypted: on-path peers can read/modify credentials, cookies and
page code. Pairing codes do not add transport confidentiality or server authentication.
**Non-loopback HTTP administration works even when browser microphone capture is
blocked.** Browser Talk needs a secure context (localhost or trusted HTTPS), browser
permission and the supported WebCrypto/IndexedDB/Web Locks APIs. The browser's
separate identity is enrolled on Connect; never paste the owner token into voice.
Serve the built UI through Vauxr, not a cross-origin Vite server.

For optional TLS, establish the proxy/certificates/trust first, then set (example):

```env
OWNER_HTTPS_ORIGIN=https://voice.example.test
OWNER_TRUSTED_PROXIES=127.0.0.1/32
```

The example name is illustrative, not provisioned DNS. HTTPS takes precedence
over HTTP. Presence of either TLS variable, including empty values, selects TLS
validation; malformed config fails closed. The server's shipped listeners are
HTTP; these variables do not configure a certificate listener. Follow the exact
[owner proxy boundary](owner-v1.md): restrict backend ports to the immediate trusted
proxy, preserve the configured Host, replace `X-Forwarded-Proto` with exactly one
`https`, strip `Forwarded`, and forward `/api/*`, static UI and `/ws` to the HTTP
listener and `/channel` to the channel listener. Use the configured owner authority
on the TLS channel upgrade too. Never forward plaintext credential submissions;
an HTTP redirect cannot undo disclosure. Strip credential bodies, cookies and query
strings from access/error logs. Do not trust arbitrary forwarding headers.

Clients validate chain, hostname/IP SAN and dates. Establish roots independently:
browser/OS trust store, plugin Node trust configuration before startup (PR37
documents `NODE_EXTRA_CA_CERTS` and `strictTls: true`), and a reviewed firmware CA
bundle with trustworthy device time. No `-k`, warning bypass, disabled validation,
redirect-based credential flow or automatic HTTP fallback is supported. Positive
proxy/browser/firmware TLS deployment is still unverified here. Realtime signaling
and firmware downloads also require matching authenticated HTTPS endpoints; this
WS walkthrough does not establish optional WebRTC TLS acceptance.

Plan renewal before certificate expiry, monitor expiry and clock health, and keep
the hostname/origin stable. A valid renewed leaf under an already trusted root
should not require re-pairing, but test all actual clients before rollout. Root/CA
rollover requires distributing the new trust through supported client procedures
before replacing the server chain. Firmware may need a new reviewed build; no
automatic trust enrollment is provided. Public CA automation may need domain/DNS,
internet and service credentials; private CA operation needs explicit trust
distribution. No provider, price or automatic renewal service is promised. On
renewal failure repair trust/chain/time; do not downgrade. Explicit origin changes
invalidate pending work and may require unsupported client-binding transfer—plan
that separately, not as routine certificate renewal.

## Owner override and recovery

Every restart discards process-local owner sessions, even if the saved token or
override is unchanged. Login again; device/integration credentials are independent.
Owner recovery preserves settings and completed paired identities, while changing
the owner generation invalidates pending enrollment/lifecycle work.

| Situation | Operator procedure |
| --- | --- |
| Lost generated token, lost response, expired save | Private console `vauxr-owner recover` for an existing owner; `claim` while unclaimed. Complete fresh claim/save/separate login. Old generated access and sessions stop immediately. |
| Deliberately use an override | Run `vauxr-owner generate-token` on a private interactive console. Securely place its generated value in deployment secret storage as OPERATOR_TOKEN. Start/recreate the single service with it. No token in command arguments, history or source. |
| Override present on first start or later | It is authoritative; replaces generated/pending verifier. Empty/malformed values fail startup. Same override retains durable generation; changed override changes generation. Both restarts discard sessions. Browser/console claim cannot override it. |
| Replace override | Generate a fresh value, update the actual service secret, restart/recreate, log in with the new value. A → B → A never restores old sessions. |
| Remove override safely | Remove the variable from the actual service configuration and secret injection, then restart/recreate first. Status must say recovery, not environment-managed. Run `recover` with that same environment and DATA_DIR; claim/save/login with a fresh generated token. No older generated token is resurrected. |

For Compose overrides, add `OPERATOR_TOKEN: ${OPERATOR_TOKEN:?supply generated secret}`
only when intentionally using a securely injected override; remove that mapping on
removal. Add HTTPS/proxy mappings only in TLS mode. Do not use empty default mappings
for these presence-sensitive variables. Do not dump rendered Compose config or
container environment into support logs. A console missing the override does not
reconcile removal; **server startup** does. If status stays environment-managed,
check the service manager/container's injection without printing its value.

## Breaking migration and private backup

Budget downtime from stopping old writers until every intended client is re-paired,
integrations reconnected, settings checked and a real voice turn succeeds. There is
no universal duration: allow time per physical speaker and access to its button,
speaker and console. Five-minute enrollment windows are attempt deadlines, not an
upgrade-time estimate. Finish dependency downloads/builds and rehearse rollback
before the maintenance window. Do not start while compatible artifacts are missing.

1. Inventory exact old/new server/UI/plugin/OpenClaw/firmware versions and image
   digests, service account/daemon context, every mount and external state path,
   origins, trust/time dependencies, routing, device IDs/names and speech selections.
   Check free space for an independent full backup and restored copy. Record a
   rollback decision point before committing to device changes.
2. Stop the old Vauxr process, plugin/gateway writer and all other writers of the
   selected data/configuration. Stop speech services too when copying their caches.
   Close browser voice sessions and keep devices disconnected throughout migration
   or restore. Confirm no other checkout, console job, container or restart policy
   can write those stores. Do not mix old/new server or plugin writers.
3. Take one consistent, access-controlled backup with writers stopped. Preserve
   full DATA_DIR: `authz.json` and lock file, `devices.json`, `speech-settings.json`,
   `speech-providers.json`, `channels.json`, `config.json`, `webhooks.json`,
   `vauxr-identity.json`, and any other contents. Include deployment files and secret
   manager recovery material, TLS private keys/CA config and external firmware,
   recordings and model-cache mounts as applicable. Include OpenClaw configuration
   and private plugin state (`vauxr-auth/<binding hash>/credentials.json` below its
   state directory), preserving 0700 directories/0600 files. Backups contain secrets;
   encrypt offline/off-host copies and restrict access. Never attach them to issues.
4. Verify backup integrity and restore into a **new private directory**, preserving
   bytes, ownership and modes using the deployment's backup tooling. Compare a
   private checksum manifest/file inventory; keep originals immutable. A file copy
   of a running multi-file configuration is not a consistent backup. `authz.lock`
   is not a whole-installation backup lock. On a local same-account filesystem,
   `umask 077; cp -a -- "$DATA_DIR" "$BACKUP_DIR"` is a copy primitive only when
   BACKUP_DIR is a new nonexisting path outside DATA_DIR; it does not cover external
   mounts, foreign ownership, encryption or remote durability. See the disposable
   rehearsal below for byte/mode verification without production paths.
5. Start only the pinned new server/UI against the selected restored working copy,
   keeping the original backup untouched. Remove obsolete DEVICE_TOKEN and plugin
   `token` configuration; they cannot be imported as scoped access. Retain unrelated
   routing/provider configuration. No authz file is required on an old shared-token
   installation: explicit owner setup creates one. Earlier auth schemas 1–4 load
   through the current reader; schema upgrades occur as namespaces are written.
   Unknown/corrupt schemas fail closed—restore or seek help, do not hand-edit version
   fields, delete namespaces/tombstones or manufacture credentials.
6. Complete owner claim/save/login (or deliberate authoritative override setup).
   For an already claimed scoped-auth snapshot, use its saved token or console
   recovery; do not claim that every upgrade requires replacing that owner.
7. Reconnect integrations and explicitly re-pair each legacy device using the
   procedures below once firmware is build/acceptance-cleared. Existing valid v1
   clients with retained keys, server binding and credentials can reconnect to a
   compatible restored snapshot; shared-token clients cannot. Check settings by
   stable ID, then manually transfer only reviewed settings where identity changed.
8. Verify owner login after restart, no legacy token access, scoped device connection,
   actual plugin authenticated channel and active routing, speech defaults/overrides,
   one real voice exchange and permitted announcement/control. Exercise lifecycle
   acceptance in the approved test setup. Record only secret-free results and exact
   versions. Keep the backup through acceptance and the rollback retention period.

### Identity and settings transfer limits

Legacy arbitrary IDs cannot become `dev_` + SHA-256(Ed25519 public key). The Voice PE
candidate ignores old NVS `device_id`/`token` as authority; Wi-Fi/unrelated settings
are intended to survive, but actual hardware migration is not yet validated. Do
not flash defaults NVS over an enrolled device, erase its partition to resolve a
conflict, or clone a key/flash image between speakers.

With the original enrolled key, physical/browser kind and server binding intact,
owner **Recover** preserves the same ID and server settings through a fresh
five-minute grant and local proof (browser recovery uses its retained browser key).
It is not cross-installation transfer. A missing key, changed installation/origin
or legacy identity without retained proof cannot use same-ID recovery. Integration
re-enrollment creates a new channel subject; activate it explicitly and revoke
obsolete scoped integration authority.

Preserve old settings for reference. After positively identifying and pairing a
new device, apply its reviewed name, boolean `voice`, follow-up, barge-in and button
actions (and any supported output sample rate) through owner device configuration, and its speech overrides through
owner speech settings. Do not globally replace ID strings or copy credential/binding
records. Speech `voice_id` is distinct from boolean device `voice`. Global speech
settings/catalog remain unchanged; per-device overrides stay keyed to the old ID
until explicitly reapplied. Recheck webhook targets and agent/session mappings;
the `vauxr:<device_id>` conversation identity can change. No automatic conversation,
private-key, identity or client-binding transfer tool is supplied. Browser profile
deletion loses its key and has the same limit.

For browser reconnect, reopen the same origin/profile, log in if needed, then
press **Connect**. Reload retains the browser identity but does not reconnect
automatically. Only one voice tab per origin/profile can hold the Web Lock.
Disconnect/closing the tab retains its credential; successful **Logout** revokes
this browser's scoped credential and retains its key for explicit recovery.
After revoke or lost credential delivery, use owner Recover with that retained
key before Connect; clearing site storage cannot repair same-identity access.
See [browser storage and lifecycle](browser-v1.md).

### Reconnect OpenClaw

Use the reviewed plugin checkout in its own installation workspace, not a published
store package assumed compatible. At the pinned plugin commit above, its documented
local procedure is `npm ci`, `npm run build`, then
`openclaw plugins install path:/absolute/path/to/vauxr-openclaw`. Use OpenClaw 2026.9.3
and supported POSIX private storage. See the pinned [plugin setup and commands](https://github.com/vauxr-ai/vauxr-openclaw/blob/69794b3a4a9f6859f56c0deba9fc017178639ac1/README.md)
and [SDK evidence](https://github.com/vauxr-ai/vauxr-openclaw/blob/69794b3a4a9f6859f56c0deba9fc017178639ac1/docs/auth-lifecycle.md).
This package does not install or modify a plugin or gateway.

Keep preferences such as `targetAgent`, `voiceSystemPrompt` and per-sender tool
choices. Remove old `token` fields. Set `channels.vauxr.url` to the socket base,
`httpUrl` to the exact owner origin and `otaPublicBase` to the matching HTTP/HTTPS
base; use the same hostname and mode. Retain the plugin enablement and conversation
hook permissions described by PR37. No owner token enters plugin config.

On channel startup, obtain `/vauxr status` through an authorized command surface.
Compare its eight-character code, endpoint and request identity with **Channels →
integration requests** in the owner UI; approve the intended request before expiry.
The plugin receives its own token once, privately saves/flushes/reads back, then
ACKs. `delivered` is not connected. Wait for authenticated `connected`, refresh
channels and explicitly activate that integration. `/vauxr pair` starts fresh setup
after terminal failure; `/vauxr cancel` cancels unfinished setup. These commands
require the documented authorized `operator.admin` command context.

Integrations can list/announce/control devices, initiate firmware updates and
initiate/approve fresh physical pairing with explicit participation and matching
code. They cannot configure owners, export/rotate/revoke credentials, configure
speech/webhooks/channels or recover known devices. Firmware initiation is not
publication or proof an image exists; no OTA commands are part of this walkthrough.

### Re-pair Voice PE once the firmware gate is cleared

The unbuilt candidate's intended procedure is recorded here for coordination,
**not as a validated flashing/provisioning recipe**. Configure Wi-Fi, `server_url`
and exact `auth_origin` via the firmware's supported mechanism. Separate listener
ports require explicit auth_origin. Obtain and follow the future reviewed artifact's
installation instructions; there is currently no safe concrete binary to recommend.

For an unpaired Voice PE, press and release Action after at least five seconds.
The candidate opens at most 300 seconds and speaks all eight digits locally.
Hear the complete code on the intended speaker. In owner Pairing and access,
refresh/identify the request, enter the code and explicitly confirm physical
participation for initiation and approval. An authorized OpenClaw physical pairing
flow requires the same fresh window and exact heard digits for each action.
Names/discovery alone are not consent. The device receives and saves its own
credential; neither approver sees it. Confirm actual authenticated connection;
`consumed` alone does not prove device installation. Restart/expiry needs a fresh
local hold. Known-device recovery is owner-only with the original key; the candidate
documents a ten-second release to lock an active device for recovery, followed by
owner Recover and a fresh five-second hold. Do not infer support on other boards.

## Rollback

Stop all new writers and disconnect clients first. Save a separate private snapshot
of the failed attempt if needed. Restore the **whole pre-upgrade snapshot into a
new directory**, plus its matching service configuration, secrets and exact old
server/UI/plugin versions. Restore client state only using that client's supported
procedure and mutually compatible firmware; newer device journals may have no
validated downgrade path. Without that evidence rollback of physical clients is
blocked—preserving a server tarball alone does not make it possible. Never let an
old binary touch schema 5 or run mixed writers. Do not combine old authz with new
rotation history, or selectively remove history to make a loader accept it.

Rollback loses post-backup settings/enrollments/rotation state and can resurrect
revoked authority: the store has no external monotonic anti-rollback authority.
Before allowing clients back, use a current supported snapshot when possible,
or perform security recovery with fresh owner access and replacement/revocation
of restored client credentials. For a legacy rollback use that version's own
credential-replacement process on an isolated endpoint; if unavailable, keep it
offline. Never restore a known-compromised secret or snapshot to an accessible
service. Close old sessions; restart invalidates cookies but is not client-token
revocation. Reconnect/re-pair explicitly and repeat acceptance. An old installation
restored in isolation is not a compatibility mode in this server.

## Troubleshooting and support

| Symptom | Distinguish and act |
| --- | --- |
| Startup rejects origin/override | Check canonical scheme/host/non-default port, no trailing slash; empty TLS/OPERATOR_TOKEN settings are invalid. Verify actual service injection without printing secrets. |
| Owner 403 / CSRF / Host failure | Open exact configured origin; use built same-origin UI; check cookie mode and proxy header contract. No wildcard CORS or bearer workaround. |
| Owner login fails after restart | Sessions always expire. Use saved current generated/override token; if lost use the appropriate console or secret-manager recovery path above. |
| TLS/transport error before auth | Check reachability, hostname/IP SAN, full chain, roots, expiry and clock; no warning bypass or plaintext retry. Certificate repair differs from token recovery. |
| Non-loopback HTTP Talk unavailable | Browser secure-context restriction; admin is still usable. Use localhost or explicitly trusted HTTPS for microphone access. |
| Pair code mismatch/expired/denied | Do not approve by name. Check intended request/device and complete heard code; obtain a new local window/request after terminal failure. Never extend the old deadline. |
| Offline rotation queued/pending | Queued lasts up to 24 hours; pending means the client polled, not saved. Reconnect before deadline. No delivery means expiry leaves old authority usable. |
| Rotation delivered but not completed | Up to five minutes of overlap, bounded by original expiry. Client must save and ACK with replacement. Lost ACK retries same saved operation; lost delivery/save requires explicit recovery. Without timely ACK both tokens expire. |
| Revoked or re-pair-required | Revoke is terminal. Device needs owner same-key recovery; integration needs new owner-approved enrollment. Restarting cannot revive it. |
| 429 / 503 / timeout | Back off. Preserve operation ID and refresh public status; outcome may have committed. Repair local storage permissions/space/fsync. Never delete lock/history to retry. |
| Plugin storage_error | Repair POSIX ownership/private permissions and supported SDK/durability. No manual token/config fallback. |
| Auth works, voice fails | Check authenticated channel and explicit active route, Wyoming readiness/model/voice and network. A settings/readiness check is not inference or speaker proof. |

For support use [issue #52](https://github.com/vauxr-ai/vauxr/issues/52) for this
guide or the [Vauxr issue tracker](https://github.com/vauxr-ai/vauxr/issues).
Report exact commits/artifact hashes, platform/runtime versions, selected transport
mode, redacted configuration variable **names**, fixed error/status codes, timing,
and which synthetic/physical checks actually ran. Review every log excerpt first.
Never send tokens, claim/matching codes, cookies/CSRF, request secrets/signatures,
authz dumps, plugin private state, browser profiles, NVS images, environment dumps,
TLS keys, recordings or backup archives. Do not enable auth-page tracing/screenshots.

## Disposable rehearsal and evidence boundary

From this checkout with Python 3.12 and the core dependencies installed:

```sh
python3 scripts/rehearse_auth_migration.py
python3 -m pytest -q tests/test_owner_auth.py tests/test_integration.py tests/test_lifecycle.py
git diff --check
```

The script accepts **no arguments or data path**, clears ambient environment in an
isolated child (also when invoked with `-I`), creates a fresh mode-0700 temporary
DATA_DIR and removes it on normal completion or a handled failure. Forced process
termination or host failure can leave private temporary data under `/tmp`.
A Python audit guard rejects socket operations; only synthetic settings/generated credentials are used. It
checks empty and schema 1–5 fixtures, private backup/byte-exact restore, schema-5
integration enrollment with save/readback/ACK, generated owner setup, override
precedence/change/removal, restart session invalidation, settings preservation,
no legacy auth, tombstone preservation and the risk of restoring an older snapshot.
It calls production storage/auth services; it is not a production migration tool,
old-binary test, Docker volume test or proof of live client interoperability.
See [browser verification](../../e2e/README.md) for the separately scoped real-HTTP
and Chromium suite. Actual installation, encrypted off-host backup recovery,
firmware, plugin/gateway, TLS renewal and physical acceptance remain release work.

Source review map: [configuration](../../src/config.py),
[owner implementation](../../src/owner_auth.py), [console](../../src/owner_cli.py),
[snapshot reader/writer](../../src/auth_store.py), [enrollment](enrollment-v1.md),
[lifecycle](lifecycle-v1.md), [integration](integration-v1.md),
[browser](browser-v1.md), [Dockerfile](../../Dockerfile) and
[Compose](../../docker-compose.yml). The plugin links above pin its matching
README/storage contract. Firmware procedure details were checked against the
uncommitted #69 candidate's `docs/voicepe-auth-v1.md` and implementation; its
worktree base `238d3af9b068147401573abef3091483fd78a245` is **not** an auth firmware
release or a commit containing that candidate. A publishable firmware pin is still missing.
