# Combined auth browser verification (#53)

Build from the repository root, using Python 3.12 and Node with native WebSocket
and `import.meta.dirname` support (Node 22+):

```sh
python3 -m pip install '.[dev,realtime]'
npm ci --prefix web-client
npm --prefix web-client test -- --run
npm --prefix web-client run build
npm ci --prefix e2e
cd e2e
npx playwright install --with-deps chromium
npx playwright test
```

The realtime extra is needed for the **complete backend suite**, including pinned
Pipecat 1.9.0 media authority tests; it is not needed for these Chromium scenarios.
Run `python3 -m pytest -q` from the root for backend tests. Use a virtual environment
where available; the supplied base VM alternatively uses system Python with
`pip --break-system-packages`. Never point this suite at a live installation.

`auth-combined.spec.ts` starts the real application and startup/cleanup hooks on
an OS-assigned loopback socket. Every test gets private temporary data, a clean
browser context, and an allowlisted environment. Native synthetic clients reach
real HTTP and WebSocket routes. Restart retains the selected port and data while
invalidating process-local owner sessions. Credential save/readback uses a private,
fsynced temporary file and atomic rename before ACK. This is a synthetic native
client, **not** the actual OpenClaw SDK or firmware persistence implementation.

`auth-browser.spec.ts` uses the production dual-listener server on 18080/18765,
and an isolated rejection-only TLS listener on 18443. Keep those ports free. Each
scenario gets fresh disposable data; the LAN case additionally needs a non-loopback
IPv4 interface and is explicitly skipped when none exists. Both harnesses exclude
ambient auth, proxy, routing and speech endpoint settings. Wyoming providers are
intentionally unavailable; selection/readiness is tested without inference.

Six Chromium scenarios cover:

- Console claim, deliberate token-save acknowledgement, separate owner login and
  recovery, cookies/CSRF/Origin, scoped browser key/identity, physical-client signed
  proof simulation, microphone denial, tabs, reload, offline rotation and logout.
- Non-loopback HTTP administration with browser microphone restriction; global
  provider/model-scoped voice selection and per-device inheritance/reset. This
  inherited LAN test uses a synthetic device-list projection; speech calls are real.
- Untrusted HTTPS/WSS rejection with verification enabled and no request reaching
  the untrusted endpoint. This is not positive trusted browser TLS acceptance.
- Owner integration approval, no access before save ACK, one-time delivery and
  lost-ACK retry, scoped integration/device/owner denials, synthetic physical pairing
  approved with integration authority, real channel authentication/activation,
  actual connected-device speech settings, rotation and immediate socket revocation
  while the device remains authorized. No device/owner credential enters approval.
- Mismatched integration code, explicit denial, cancellation, duplicate display
  names, and absence of the removed shared-token/channel-create/export forms.
- Server restart with stale owner-cookie rejection, saved-token login, preserved
  integration access and per-device speech configuration.

Tracing, video, screenshots and Playwright's automatic failure DOM snapshots are
disabled because claim/login pages contain one-time secrets. Do not enable them
for these workflows. Assertions on credentials compare booleans rather than
printing secret values. Generated credentials and certificates are disposable and
removed by teardown. CI runs all specs; the obsolete shared-token `channels.spec.ts`
was replaced by the real owner/integration workflows.

Physical Voice PE pairing/button/audio, firmware build and power-loss acceptance,
actual plugin-plus-browser operation, real inference/RTP/OTA, optional trusted
browser TLS provisioning, and migration rehearsal remain separate acceptance.
See [combined handoff and release gaps](../docs/authz/combined-53.md).
