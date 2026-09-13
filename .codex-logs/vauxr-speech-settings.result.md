# Bounded STT/TTS neutrality follow-up review

## Scope and starting state

Reviewed only the existing isolated `feat/speech-provider-settings` worktree,
starting clean at `ac2b71a`. Read AGENTS.md, CLAUDE.md, ARCHITECTURE.md and
ROADMAP.md, then inspected the actual flat src layout, configuration, catalog,
shared Wyoming clients, resolver, WS/WebRTC/button/announcement call sites,
management API and existing tests. Prior feature commits remain intact.

## Already implemented and verified

- `speech_catalog.py` accepts generic `wyoming` for either STT or TTS and retains
  whisper/parakeet-v3/piper/kokoro aliases. `speech.py` delegates validation to
  that catalog; it does not restrict routing to four engines.
- `Config.stt` / `Config.tts` and `WyomingTTSConfig` are neutral. Nonempty
  STT_URL/TTS_URL/TTS_VOICE take precedence over WHISPER_URL/PIPER_URL/PIPER_VOICE;
  empty/unset values fall back to legacy values, then unchanged defaults.
- Case-insensitive source search finds the four engine names only in the catalog
  compatibility boundary. Shared clients and routing do not branch on model IDs.
- Catalog built-ins retain IDs/order. Persisted defaults, device overrides,
  model-scoped voices, reset/inheritance and explicit unresolved-selection errors
  remain. Existing migration tests check identical selections and file bytes.
- WS voice/text, buttons, announcements and cold/warm WebRTC use the shared
  resolver and complete immutable selections. Existing tests cover mid-turn
  edits, next-turn resolution and overlapping Pipecat reply contexts.
- Management accepts only configured IDs/voices, hides endpoint addresses and
  rejects endpoint fields. No browser endpoint inputs or plugin framework added.

## Changes in this completion commit

- Compose now forwards neutral env variables and legacy overrides to the gateway.
  Previously its fixed legacy values prevented .env speech overrides from reaching
  the process. Unconfigured loopback URLs and default voice remain unchanged.
- Updated .env.example, README and speech settings docs with preferred variables,
  precedence, standalone versus Compose defaults and generic opaque deployments
  for both kinds, retaining the legacy adapter examples.
- Strengthened the fake Wyoming wire test: generic opaque STT/TTS deployments and
  narrator voice IDs now load through speech-providers.json, catalog validation,
  persisted device selection and store restart before real local TCP requests.
  Checks both ASR/TTS readiness, transcript, PCM, voice.name and endpoint hiding.
- Added neutral-only env and all-empty/all-unset default regressions alongside
  existing conflicting-value precedence, legacy-only and restart migration tests.
- No runtime source changes were needed after review. No auth, firmware or
  frontend implementation changes; no installed plugins modified. No push,
  publication, deployment, branch switch or other worktree edits performed.

## Validation evidence

Existing Python 3.12 .venv contains optional Pipecat; no dependencies installed.

- `.venv/bin/python3 -m pytest -q`: **347 passed**, no skips, 3 dependency
  deprecation warnings, 8.00s.
- After strengthening opaque voice names, reran the affected tests:
  `.venv/bin/python3 -m pytest -q tests/test_speech.py tests/test_config.py`:
  **32 passed**, 2 dependency deprecation warnings, 0.25s.
- `npm --prefix web-client run test -- --run`: **125 passed in 12 files**.
- `npm --prefix web-client run build`: **passed** (TypeScript and Vite).
  Browserslist reported stale caniuse-lite data; no dependency updates made.
- Focused Ruff covering config, speech registry/catalog/models, Wyoming protocol
  and clients, realtime adapters and speech/config tests: **passed**. Affected
  tests passed Ruff again after the final test refinement.
- `git diff --check`: **passed**. Reviewed the complete diff for defaults,
  legacy compatibility, catalog loading and scope regressions.
- Compose YAML parses and includes all six speech env keys. Actual
  `docker compose config` validation was attempted but **unavailable**: the Docker
  CLI has no Compose subcommand. No Compose interpolation or deployment success
  is claimed. Browser E2E was not run; frontend code is unchanged.

## Remaining neutrality and performance limitations

Generic support requires an operator-provisioned Wyoming service implementing
Vauxr's existing PCM/transcript or synthesize/voice.name contract. Opaque model
labels do not load models or switch remote deployments. Voices must already be
available remotely. No arbitrary protocols, model installation or discovery are
provided; the catalog remains bounded to 32 entries including legacy built-ins.
Readiness is only a bounded describe capability probe, not inference validation.
The Compose stack still provisions and depends on bundled Whisper/Piper even
when an external service is selected. Existing PCM/resampling assumptions remain.

No live model inference, GPU, ESP32 audio or WebRTC media exchange was tested.
WebRTC TTS still buffers an entire segment before Pipecat playback. WebRTC
streaming performance is explicitly out of scope; no latency/performance claim
is made. Current legacy management authorization remains unchanged, with its
limitations and future scoped-auth integration documented in speech-settings.md.
