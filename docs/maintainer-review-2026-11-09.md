# Maintainer review — 2026-11-09

## Revision and scope

Reviewed `vauxr-ai/vauxr` develop revision
`25469df63362853a2a4daf18cc0a115c6626b6bf` in an isolated
`docs/maintainer-review-20261109` worktree. This is a **bounded architecture and
contract review**, not an exhaustive security/line-by-line audit. Changes are
prose/comments only; no merge, deployment, behavioral repair or live-device test.
No open PRs were returned by the repository API at review start.

## Coverage ledger

“Trace” means selected implementation paths and callers/tests were read;
“survey” means file/module interfaces or configuration were inspected, not every
function body. Paths are relative to the repository root.

| Subsystem | Inspected evidence | Depth / remaining gap |
|---|---|---|
| Gateway and wire protocol | `src/vauxr/server.py`, `protocol.py`, `__main__.py`, `pipeline.py` | Trace admission, dispatch, cold handoff, binary capture, startup and listeners; pipeline helpers sampled, not all turn/error branches |
| Authorization | `src/vauxr/auth/policy.py`, `web/owner.py`, `web/server.py`; `auth/` interface survey | Trace owner boundary, operation mappings and principal resolution; credential-store crash/concurrency internals not audited |
| Provisioning | `src/vauxr/provisioning/*.py` module interfaces, browser enrollment caller | Survey service/HTTP/schema split; signed transcripts and lifecycle recovery not independently exhaustively reviewed |
| Agent routing | `src/vauxr/agents/server.py`, `registry.py`; other `agents/*.py` interfaces | Trace peer/run/request correlation, local cancellation, metadata/authority split; direct gateway handshake, local LangChain/support adapters and avatar processing remain sampled |
| Realtime/Pipecat | `src/vauxr/realtime/startup.py`, `app.py`; `realtime/*.py` interfaces and selected session references | Trace WS/RTP FIFO ownership, limits, pacing and readiness; full live provider pipeline, output jitter/gain, media watchdog and diagnostics not audited |
| Speech/providers | `src/vauxr/speech/store.py`, `models.py`, `catalog.py` references; `speech/*.py`, `settings/*.py` interfaces | Selection/storage survey; external provider network/error behavior, resampling and secrets-at-rest internals not exhaustively reviewed |
| Device services | `src/vauxr/devices/settings.py`, `config.py`/`registry.py` references; all `devices/*.py` interfaces | Trace shared defaults and connection ownership; firmware delivery, metrics and outbound identity cryptography surveyed only |
| Web management/plugins | `src/vauxr/web/*.py`, `plugins/*.py` interfaces | Middleware/routes traced; webhook execution, companion, preview and bundled-agent implementations remain survey-level |
| Browser | `web-client/src/auth/browser.ts`, `auth/api.ts` references, `hooks/useWebSocket.ts`, `useHttpApi.ts`, `voiceProtocol.ts`; `web-client/src/` inventory | Trace owner/device identity split and storage ACK ordering; React components, hooks/audio workers, CSS/accessibility and visual acceptance not exhaustively reviewed |
| Tests/CI | `tests/test_protocol.py`, `test_authz_transports.py`, `test_realtime_startup.py`, owner/agent auth and provider-boundary focused tests; `.github/workflows/ci.yml`, CI script checks, E2E inventory | Focused tests executed below; remaining Python/Vitest suites and all browser domains not executed locally |
| Packaging/docs | `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `LICENSE`, README, AGENTS, CLAUDE, ARCHITECTURE, docs inventory | Entrypoint/default/license/document links checked; no wheel/image build or dependency supply-chain audit |

Exclusions: lockfiles, binary assets, bundled `plugins/openclaw-*.tgz`, generated
build outputs and dependencies. `firmware/` contains no tracked files at this
revision. ROADMAP, DESIGN, config examples and legacy Postman coverage are not
certified by this review. The inventory traversed first-party subsystem ownership;
it does **not** imply every first-party file was deeply reviewed.

## Peer contracts (read-only adjacent checkouts)

- `vauxr-openclaw` at `e0f6c103f904363da431f864c8b39712ee1dc329`:
  `src/transport.ts` derives `/agent` (8765 → 8080 for default management URL),
  refuses mixed hosts/transports and validates TLS. `src/bridge.ts` authenticates
  with `agent.auth`, validates `agent.ready.agentId`, sends device/run-correlated
  Standard replies, and returns request-correlated realtime results. Compare
  `src/vauxr/agents/server.py`: pending realtime results bind request ID, connection
  and device. Standard response admission uses connection/device/run history and
  current listener ownership. No claim of arbitrary-version compatibility.
- The bridge serializes non-consult realtime work per device and deduplicates
  requests by device/session/request ID. Server waiter cancellation does not send
  a cancellation operation; a consultation/tool action may still finish after
  media teardown. Retrying must account for uncertain external action outcome.
- `vauxr-assistant` at `d243f7ac632f49946599d72b2cdcc3b6464a6402`:
  `components/vauxr_client/vauxr_client.cpp` (`runRealtimeSession`) stops startup
  capture before WebRTC media and sends `realtime.media_ready` with startup ID and
  next sequence. `SignalingClient.cpp` carries the same ID in the offer.
  Server `StartupAudio` rejects sequence gaps and drains WS before held RTP once
  provider input is ready. This contradicts the previous architecture's batch
  `voice.end.webrtc_connected` description. Physical capture/playback and network
  impairment behavior were not exercised.

## Findings

1. **Confirmed documentation gap (medium): missing auth documentation.** README,
   AGENTS and other docs reference `docs/authz/*`, but the reviewed tree contains
   only `docs/configuration.md`. Exact-origin owner setup/migration instructions
   therefore lead to missing files. Reproduce with `git ls-tree -r HEAD docs`.
   Restore approved canonical auth guides in a follow-up; this review deliberately
   does not reconstruct a security-sensitive migration procedure from partial
   implementation evidence. Updated architecture links to existing sources.
2. **Confirmed metadata contradiction (medium): distribution license.**
   `pyproject.toml` declares MIT and an MIT classifier while `LICENSE` and README
   declare AGPL-3.0. Building/publishing the wheel exposes inconsistent license
   metadata. Maintainer must decide authoritative licensing and reconcile it;
   no license or executable packaging metadata was silently changed.
3. **Implementation risk (medium, no load reproduction): unbounded Standard capture.**
   `server.handle_binary()` appends each authenticated listening microphone payload
   to `ctx.audio_chunks`; `_voice_end()` consumes the list. A scoped device that
   sends `voice.start` followed by continuing audio without `voice.end` can grow
   retained memory across arbitrarily many frames. Per-frame WS limits do not
   bound total capture duration. Unlike `StartupAudio`, this path has no cumulative
   audio/duration cap in the inspected branch. Validate in a disposable process
   with repeated bounded frames; consider a duration/byte limit and timed cleanup.
4. **Confirmed architecture/comment drift, reconciled here.** Node/flat-module
   entrypoints, `/channel`, first-byte dispatch, 400 ms defaults, duplicate
   segmentation prose and batch text-seeding descriptions no longer matched the
   implementation. Current map and narrow parser comments replace those claims.

These are not a complete vulnerability list. No functional defects were repaired.

## Validation

- Python 3.12 environment with optional Pipecat installed:
  `python -m pytest -q tests/test_protocol.py tests/test_authz_transports.py tests/test_realtime_startup.py`
  → **234 passed**, one `audioop` deprecation warning (9.30 s).
- Initial system Python 3.11 invocation failed collection because NumPy was absent;
  that unsupported environment result was not treated as a product regression.
- `node --test scripts/playwright-domains.test.mjs scripts/design-check.test.mjs`
  → **3 passed**; `git diff --check` and local link-target checks for architecture
  and this review passed. Markdown/Mermaid was source-reviewed; GitHub rendering
  has not been visually verified.
- Additional focused run of `test_owner_auth.py`, `test_agent_token_auth.py` and
  `test_startup_provider_boundary.py` → **181 passed, 3 failed**. Failures were
  provider-order cases `[False]`/`[True]` and provider-rejection/retry: the local
  provider socket did not receive `session.start`, after Pipecat logged
  “did not receive a valid HTTP response”. Baseline comparison recorded below.
- No local full Playwright run, live deployment, physical device interaction,
  browser rendering verification, full Python/Vitest suite, image or wheel build.
  CI state must be checked on the published PR revision; local focused success is
  not full acceptance.
- Exact pinned baseline exported with `git archive` and tested with the same
  Python 3.12 interpreter/dependencies: `test_startup_provider_boundary.py`
  → **8 passed, the same 3 failures** (14.80 s). These failures are reproduced
  without the documentation/comment edits; their environmental versus product
  cause remains unresolved. They do not establish provider acceptance.
- Parser executable AST comparison (docstrings stripped) against the base passed.
