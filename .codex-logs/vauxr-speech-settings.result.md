# Provider-neutral speech follow-up

Worktree: `feat/speech-provider-settings`. Inspected AGENTS.md, CLAUDE.md,
ARCHITECTURE.md, ROADMAP.md, speech implementation/tests/docs, status and history.
Started clean at `2889e86`, following implementation `2375dd5`. Prior commits are
preserved. This artifact accompanies one local follow-up commit using configured
git identity, without coauthor trailers. Nothing was pushed.

## Already neutral

- Management selects configured backend/voice IDs; endpoints are server-owned.
- Global/device inheritance, model-scoped voices, persistence and immutable
  complete selection snapshots already existed.
- Device WS turns, text/button prompts, announcements, cold WebRTC buffered
  seeding/WS fallback and warm WebRTC turns already used the shared resolver.
  STT, reply segments and spoken backend errors already received the snapshot.
- Pipecat already used the common Wyoming STT/TTS clients and retained selections
  by reply context. This follow-up does not claim to introduce that routing.

## Fixed

- Generic `Config.stt`, `Config.tts`, `WyomingTTSConfig`; neutral server env names.
- Generic deployment/snapshot models in `speech_models.py`; server catalog,
  legacy mapping, adapter validation and voice wire mapping in `speech_catalog.py`.
- Initial store defaults select the first configured backend of each kind,
  rather than requiring hard-coded model IDs. Wrong-kind persisted selections
  fail explicitly. Generic `wyoming` catalog entries support opaque model IDs
  using the existing wire contract; no generalized plugin system was added.
- Shared event framing and `WyomingError` in `wyoming_protocol.py`. TTS/readiness
  no longer import protocol machinery from the STT client. STT protocol imports
  remain available for existing Python callers. Explicit remote error events now
  fail immediately without forwarding remote payloads; premature EOF also uses
  the generic RuntimeError subclass.
- Removed model-specific transport descriptions and TTS sample-rate variables.
  Case-insensitive source search finds model-specific names only in the catalog.

## Compatibility and boundaries

Nonempty `STT_URL`, `TTS_URL`, `TTS_VOICE` take precedence over `WHISPER_URL`,
`PIPER_URL`, `PIPER_VOICE`; empty/unset preferred variables retain legacy fallback
and unchanged deployment defaults. These are server-only environment settings,
not new client-supplied endpoint URLs. Catalog loading still prepends the legacy
built-ins, preserving their IDs/order and initial behavior. Existing operator
catalog files and persisted settings retain their schema and selections; a test
migrates env names and verifies both selection equality and unchanged file bytes.
Removed IDs/voices remain explicit resolution failures, without silent fallback.

No auth scope/boundary, installed plugin, deployment, compose, host driver or
public remote changes. No automatic downloads, model provisioning, browser-owned
endpoints, arbitrary endpoint API, firmware or frontend changes.

## Validation

Existing local Python 3.12 `.venv` includes optional Pipecat; no dependencies were
installed for this task.

- `.venv/bin/python -m pytest -q tests/test_speech_neutral.py tests/test_config.py tests/test_speech.py tests/test_wyoming_stt.py tests/test_wyoming_tts.py`
  — **53 passed, 2 warnings in 0.62s**.
- `.venv/bin/python -m pytest -q`
  — **344 passed, 3 warnings in 7.87s**, no skips.
- `npm --prefix web-client run test -- --run`
  — **12 test files passed, 125 tests passed**, 1.53s.
- Focused Ruff check of changed functional modules and tests — **passed**.
  Broader check also included wording-only pipeline/realtime files and reported
  18 existing findings. Compared their codes, locations and messages against
  `git show HEAD:<file>`: all unchanged (pipeline 8, session 9, turn strategy 1).
- `git diff --check` — **passed**.
- Frontend build/UI browser smoke not run: no frontend code/assets changed.

New regressions use a registry without legacy IDs to exercise WS voice routing,
cold WS fallback, button prompt, button announcement, direct announcement,
realtime buffered/text seeding and real Pipecat segmented STT/TTS adapters.
They check device selection, fixed reply snapshots despite settings edits, and
next-turn resolution. Cold fallback deliberately resolves again to arm the next
turn. Local fake Wyoming peers verify generic successful STT/TTS, readiness,
wire voice names, and prompt failure on error events from a peer that stays open.
Other regressions cover defaults, legacy env precedence/empty fallback, restart
migration, and incompatible persisted selection kinds.

Warnings are dependency deprecations: audioop, AudioContextTTSService and the
existing VAD turn-stop reset override.

## Remaining real provider/hardware limitations

No real Whisper/Parakeet/Piper/Kokoro inference, GPU execution, ESP32 audio,
WebRTC media exchange or latency benchmarks were performed. Generic catalog
support requires an operator-provided Wyoming service implementing the existing
PCM/transcript or synthesize/voice.name contract. Models and voices must already
be provisioned on that service. Readiness is a bounded describe capability probe,
not proof of model/voice availability or inference success. WS streams received
PCM; realtime TTS still buffers each entire segment before Pipecat playback.
Resampling and existing PCM assumptions remain unchanged.
