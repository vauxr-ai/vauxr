# Combined server/UI auth groundwork (#53)

This branch is stacked on **unmerged PR60 and PR61**, over **merged PR58**:

- develop/PR58: `8ffc9110f0fefcf285b822e7829dcb345ba6b33f`
- PR60 browser reviewed head: `b8932451aa8edc9ada97692c09dee675af28754c`
- PR61 integration reviewed head: `16968a73b7610c917a9922d94d8c7ef187f7dda3`
- Read-only plugin PR37 context: `69794b3a4a9f6859f56c0deba9fc017178639ac1`

Both reviewed server repository heads are ancestors, preserved by explicit merge
commits on the isolated combined branch. Their shared auth commits were independently
rebased, causing add/add conflicts. Resolve the shared backend to PR61's final
schema-5 integration/lifecycle/media authority code; retain PR60's final browser
state, session invalidation, capture cancellation, same-origin HTTP/WS and CI work.
Retain the explicit owner-only `speech.configure` operation from PR60 and the
injected route boundary from PR61. Both branches' speech authorization tests remain.
No speech payload, model catalog, provider implementation, inference selection or
persistence contract changes. No auth fixture changes. HTTP/WS is still the LAN
default; optional HTTPS/WSS retains strict verification, exact selected authority
and no downgrade. An owner bearer never substitutes for a device/integration token.

The combined UI adds an owner approval section under **Channels**. Start setup in
the intended integration, refresh requests, and match its request ID, server and
code. Explicitly confirm the setup before approving. Names are untrusted labels.
Approval returns public state only. The client receives its credential once and
must save/read back before ACK. Delivered is not saved; completed means the client
asserted durable save. Refresh channels and explicitly activate the route. Existing
rotation/revoke controls remain under **Connection → Pairing and access**. Denial
and refresh handle uncertain outcomes without automatic approval or credential
redisclosure. Codes exist only in component memory and clear after submission.

Run the complete [automated browser suite](../../e2e/README.md) against disposable
servers. It tests real application routes and Chromium, including integration
approval and connected-device speech settings. The plugin protocol is simulated
from the unchanged versioned fixture/contract; this branch does not modify, run or
claim acceptance of the installed plugin. Synthetic pairing proves a key/code
exchange, not a physical button or spoken-code observation. HTTP traffic remains
unencrypted; authentication and pairing do not conceal it from a LAN attacker.

## Verification and release boundary

This is bounded automated groundwork, **not completion of #53 or a release**.
Physical firmware build/acceptance is explicitly blocked in its separate firmware
lane. Automated work is independent of that block. Before release, #53 still needs:

- Real supported Voice PE clean-install pairing, local button/audio confirmation,
  voice exchange and separately OpenClaw-approved pairing with a fresh window.
- Actual plugin PR37 plus combined owner UI/server acceptance and speech/media
  roundtrips. Automated raw-track tests are not browser/device RTP interoperability.
- Firmware durable storage and real power loss around delivery/save/ACK, online/
  offline rotation, reconnect/restart and immediate device playback revocation.
- Hardware firmware initiation/installation evidence; permission tests do not prove
  an image was built, downloaded or installed. No OTA is authorized here.
- Optional trusted browser/firmware TLS setup and renewal acceptance with valid
  certificates and wrong-name/expired/untrusted rejection; no warning bypass.
- Existing-install migration rehearsal following #52, with settings/identity
  preservation limits, explicit re-pair/reconnection and secret-free evidence.

#52 supplies the [operator-ready migration guide and disposable rehearsal](migration-52.md)
for cross-repository clean install, breaking upgrade, compatible source pins,
backup/restore, recovery and troubleshooting. Its offline service-level rehearsal
does not close the real-client or physical acceptance gaps above.
Owner setup needs no auth environment variable; optional OPERATOR_TOKEN generation,
authoritative override/change/removal and session invalidation are described in
[owner v1](owner-v1.md) and covered by backend tests. This combined #53 package itself is not a migration rehearsal.
Legacy arbitrary device IDs cannot silently become Ed25519-derived identities;
same-key recovery does not transfer an unrelated legacy identity's configuration.
A reviewed migration procedure must state where explicit settings transfer is needed.

Prepare a consistent private backup of the data/configuration with writers stopped
before any eventual authorized upgrade. Schema 5 is incompatible with old binaries;
do not selectively delete namespaces/history or mix writers to roll back. A full
old snapshot restore also rolls back revocations and is a security recovery event,
requiring fresh credentials and stopped old sessions. Confirm compatible server,
plugin and firmware versions, expected downtime and explicit reconnect steps before
an independently authorized rollout. No live backup, deployment, merge, service
change, plugin edit or firmware action is performed by this groundwork.
