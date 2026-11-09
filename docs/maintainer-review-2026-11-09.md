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

### Follow-up depth (same server implementation pin)

Read-only follow-ups at documentation head `b1ae831` compared implementations to
`25469df`; the reviewed implementations were unchanged. The following supersedes
survey-only depth above, not the remaining exclusions.

| Area | Additional implementation/body coverage | Remaining limits |
|---|---|---|
| Auth/provisioning | Entire `auth/{cli,owner,store,service,connections,policy}.py`, enrollment/lifecycle/integration services, HTTP adapters and schema validators; owner handler and web middleware | Pairing audio/prompt bodies, external proxy/TLS behavior, comprehensive test reread |
| Realtime/Standard | Full `realtime/{session,live,app,llm}.py`, `pipeline.py`, segment queue and idle segmenter; queue/app tests and selected Live/startup/media-authority tests | Remaining test bodies indexed only; physical provider/device behavior unverified |
| Browser | Audio and realtime hooks/worklet ownership, agents/webhooks hooks, VoiceSample/preview boundary, Companion context/ACK and focus behavior; ProviderSettings load/secret paths | Most components, complete App effects, CSS/accessibility rendering, other workers/assets remain sampled or unreviewed |
| Providers/adapters | OpenAI/Wyoming adapters, provider settings/HTTP boundary, webhooks/companion, complete direct OpenClaw handshake/chat/reconnect and LangChain adapter | Live auth/environment and actual network/provider outcomes not exercised |
| Bundled plugins | Agent definition/import validation, household implementation; settings registration/public-secret contract; local adapter routing | Plugin-store mutation internals and local runtime validation ownership sampled |

Adjacent auth follow-up read the entire plugin `src/auth.ts` at `e0f6c103` and
firmware `main/auth/DeviceAuth.cpp` plus selected `Auth.cpp` enrollment/install/
lifecycle branches at **`68933e1aa2b4554a3e4feae6759932f428f04625`**. This newer
firmware pin is distinct from the earlier startup trace below. Full firmware
journal/hold/error helpers and supported SDK secret-store internals remain gaps.
Auth test cases were inventoried, not rerun in this follow-up. Browser audio/
realtime tests, OpenClaw fake-gateway tests and selected LangChain/speech tests
were read, not executed by that reviewer.

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

1. **Confirmed stale navigation (medium, repaired).** README referenced removed
   `docs/authz/*` guides. History records their intentional deletion in
   `40e680a5cdb277fe57700a40bffe821906bc744c`; this is not evidence they should be
   recreated. README now links current setup/configuration and versioned source
   schemas/policy, without promising obsolete migration support. Existing
   configuration documentation explicitly states legacy files are not migrated.

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

### Additional bounded findings

All entries below require separate behavioral work; none was repaired here.
Source-confirmed control flow is distinguished from reproduced impact.

| Impact / confidence | Evidence and failure condition | Validation or repair direction |
|---|---|---|
| Medium, source-confirmed | `agents/openclaw.py::_reconnect_after`: a failed delayed connection attempt logs and stops; no reader remains to schedule another retry | Simulate disconnect followed by connection refusal and later gateway recovery; do not promise persistent retry |
| Medium, source-confirmed | `pipeline.py::_route_via_openclaw_direct` waits for `chat()` before checking abort; chat has no deadline/finally listener cleanup. A connected silent gateway can retain abandoned work | Exercise abort with no final event and inspect pending/listener cleanup; output ownership prevents stale writes, so this is not demonstrated cross-turn contamination |
| Medium, reproduced in isolation | `speech/wyoming_tts.py::_make_resampler` keeps filter state but uses chunk-local interpolation/count rounding. Identical 100 samples at 24k→22.05k yield 92 samples whole versus 100 in one-sample fragments; OpenAI TTS shares it | Add chunk-invariance regression; no physical audibility claim |
| Medium risk, source-confirmed | `speech/wyoming_stt.py` queues capture before unbounded `writer.drain()`; connection and transcript timeouts do not cover upload | Test a non-consuming provider with bounded capture; no load/exhaustion reproduction |
| Medium, bounded characterization | `realtime/llm.py::_run_turn` cancellation awaits idle pump without sentinel/abort. Actual pinned Pipecat fallback completed at 1.0 s with warning, without stopped metrics or response-end frame | Test installed pipeline interruption cleanup; not an indefinite deadlock in the normal pinned path |
| Medium risk, source-confirmed | `SegmentQueue._items`, realtime delta/pump queues are unbounded; idle flushing disabled can retain whole response. Serial synthesis does not provide bounded producer backpressure or forcibly cancel current synthesis | Bound producer/consumer test before a separate buffering/cancellation change; no practical exhaustion demonstrated |
| Medium risk, source-confirmed | Standard sample-rate callbacks schedule `audio.start` via `create_task` independently of awaited PCM; an immediate-yield synthesizer can send PCM first | Add transport ordering regression; no demonstrated firmware failure |
| Medium, source-confirmed | `pipeline.run_voice_turn` STT error returns after `STT_ERROR` without `audio.end`; current test asserts only the error | Verify device recovery separately; do not promise terminal audio control for every failure |
| Medium risk, source-confirmed | `provisioning/enrollment.py` shares a global 60-request/minute budget across enrollment, integration and lifecycle HTTP, charged before parsing/auth. Firmware polls lifecycle every 60–65 s and pairing every 10–15 s | Test anonymous starvation plus fleet polling/ACK capacity; no load/exploit result. ACK overlap is 300 s |
| Medium recovery risk, cross-pin trace | Firmware `Auth.cpp` persists four ACK attempts, decremented before requests including failed/lost replies; server lifecycle ACK is idempotent after completion | Test four lost replies including a committed ACK; firmware can enter repair despite server completion. Plugin retains/recommits rotation before retrying |

### Authority distinctions

- `enrollment.py`'s `physical_verified` is a human/code boundary, not hardware
  attestation or proof of a button press. Physical-kind requests may be anonymous;
  key proof establishes possession. Initiator and approver need not differ.
- `auth/store.py` tombstones override restored credential records, but restoring
  an entire old authority snapshot can restore old authority. The existing
  `test_tombstone_is_independent_of_record_id_but_not_full_backup` names that limit.
- `web/server.py::_require_auth` applies `secure_request()` to generic integration
  management only in HTTPS owner mode; provisioning always applies it. Exact
  owner-authority transport checks are not universal for LAN bearer APIs. This
  split needs a policy decision, not a silent documentation promise.
- Provider secrets are separate permission-restricted atomic plaintext JSON, not
  encrypted-at-rest. Named webhooks disable redirects/environment proxy use and
  have a five-second total timeout; HTTP success is not external-state proof.
- LangChain action deduplication is per turn, not exactly-once across retries.
  Live history records unconfirmed delivery, not playback receipts; standard
  media authority ends on the exact response-end marker plus transport consumption,
  not idle debounce alone.

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

### Follow-up verification evidence

- Realtime reviewer: focused set **37 passed, 1 local WebSocket connection
  failure**; that failed Live receive case passed when ambient proxy variables
  were removed for its command. Two bounded cancellation characterizations used
  actual `_run_turn` and installed Pipecat task management. This does not resolve
  the separately recorded three provider-boundary baseline failures.
- Provider reviewer: read-only AST-extracted resampler characterization produced
  the sample counts above; no application import or provider call.
- Auth reviewer: source/contract review only, no new tests executed. Suggested
  restoration of deleted auth guides was not adopted: existing canonical setup
  and source references were retained instead.
- Final documentation pass: **24 local Markdown links checked**, including
  target headings/anchors with inline-code punctuation removed for GitHub-style
  slugs. Changed Markdown only; untouched stale references (including AGENTS) are
  not certified. Python syntax compilation and docstring-stripped executable AST
  comparison against `25469df` passed for `protocol.py` and `agents/openclaw.py`;
  `git diff --check` passed. No new runtime/full-suite run in this final pass.
