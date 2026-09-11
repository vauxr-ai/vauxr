# Realtime log health — review report

## P2 review corrections — 2026-09-11

The existing feature changes below are retained. The two diagnostic regressions
identified in `../reports/vauxr-realtime-review.md` are corrected:

- Audio track-status changes are gated by the existing ESP32/no-SCTP mode policy;
  browser-mode sessions preserve track-status ownership, including disabled tracks.
- `_send_json()` reports local send success (not remote acknowledgement).
  Diagnostics stay enabled during delivery and after missing/closed sockets,
  `ConnectionResetError`, or `RuntimeError`, even if an earlier turn was quiet.
  Only a successful `audio.end{follow_up:false}` can enter intentional quiet.
  A generation check prevents a pending send from overwriting intervening speech
  or follow-up activity; cancellation also leaves diagnostics enabled.

Current validation used an isolated `/tmp/vauxr-p2-py312` environment with Python
3.12.14 and Pipecat 1.9.0, without Docker or live-service access:

- `/tmp/vauxr-p2-py312/bin/python3 -m pytest -q tests/test_realtime_log_health.py tests/test_realtime_turn_taking.py`
  — **38 passed**, two upstream deprecation warnings.
- `/tmp/vauxr-p2-py312/bin/python3 -m pytest -q` — **269 passed**, the same warnings.
- `/tmp/vauxr-p2-py312/bin/ruff check src/realtime_transport.py tests/test_realtime_log_health.py src/realtime_wyoming.py`
  — passed. All 11 remaining LLM/session findings predate this work, verified
  against HEAD by rule and message (HEAD has one additional import-order finding).
- `git diff --check` — passed.
- `timeout 20s /tmp/vauxr-p2-py312/bin/python3 ../reports/vauxr-validation-20260911/failure_teardown.py`
  — experiment completed; **repeated close timed out with adapter off and on**.
  The peer reported closed while its close-completion future remained pending.

The baseline failed-handshake teardown hang documented in
`../reports/vauxr-realtime-validation-rollout.md` remains unresolved. These P2
corrections do not change teardown or claim to fix it. Tests verify actual Pipecat
audio timeout/dead-track diagnostics with simulated media; they do not establish
physical browser/ESP32 acceptance. No web tests/builds were rerun for these P2
corrections. No live services, firmware, plugin, Docker/compose or authentication
were changed. No push or PR was made.

## Original implementation and historical validation

Prepared on `fix/realtime-log-health` on 2026-09-11. Changes are uncommitted and
ready for review. No push or PR was made. No live restart, recreation or deployment
was performed. Authentication, credentials, data volumes, firmware and plugin were
not changed. No agents were spawned and no API key was requested or configured.

The running container was confirmed to use `vauxr:nova-b45f62f` and Pipecat 1.9.0.
Its installed package source was copied out for read-only inspection. The reported
living-room ICE/pre-roll/OpenClaw/TTS success is the user's live baseline, not a new
end-to-end voice test performed during this task.

| File | Reviewable change |
| --- | --- |
| `src/realtime_wyoming.py` | Supply complete STT/TTS settings stores with `None` for unsupported runtime controls. Existing Wyoming endpoints, Piper voice, PCM handling and synthesis remain unchanged. |
| `src/realtime_llm.py` | Explicitly initialize all inherited LLM settings to `None`; channel routing continues to own model, prompt and sampling configuration. |
| `src/realtime_transport.py` | New per-connection ESP32 adapter cancels an existing unused data-channel watchdog, clears queued messages, and disables its future watchdog/message hooks. Browser-mode connections retain Pipecat behavior. |
| `src/realtime_session.py` | Apply the adapter only in ESP32 mode. Mark audio input intentionally quiet on `audio.end{follow_up:false}`; restore its timeout diagnostics on promoted speech onset or explicit follow-up. |
| `pyproject.toml` | Pin Pipecat to the verified live version, 1.9.0. |
| `tests/test_realtime_log_health.py` | Six focused checks using actual Pipecat classes, with simulated media/peer inputs. |

Pipecat's `services/settings.py` defines `NOT_GIVEN` as an omitted delta field,
not a valid settings-store value. `AIService.start()` calls `validate_complete()`;
it logs errors for missing fields without necessarily stopping the pipeline.
Explicit unsupported values fix initialization instead of filtering those errors.

Pipecat's `SmallWebRTCConnection._handle_new_connection_state()` unconditionally
starts a data-channel watchdog after connection. There is no public opt-out in
1.9.0. The new adapter changes only two hooks on the selected connection instance;
it leaves media, ICE/DTLS failure handling and peer teardown with Pipecat. It also
survives the close/reinitialize sequence used for peer restart.

In `SmallWebRTCTrack.recv()`, the enabled flag gates video reads only: audio still
reads normally. `SmallWebRTCClient.read_audio_frame()` checks that flag before
warning about an audio timeout. The session uses this existing behavior for
warm-quiet, without disabling the RTP receiver, injecting silence or dropping PCM.
Late RTP tail frames do not re-enable warnings; a real speech turn or follow-up
does. MediaStreamError reporting and dead-track cleanup remain active even while
quiet. No global logging filters or level changes were added.

Validation used a disposable container based on `b45f62f`, with source/tests copied
into `/work`, separate test tools, and no live credentials or mounted data volumes.
The host has Python 3.11; the container supplied Python 3.12 and Pipecat 1.9.0.

- Backend: `python3 -m pytest -q` — **255 passed**, two upstream deprecation warnings.
- Focused checks: **6 passed**, including settings validation still detecting a
  deliberately incomplete store, browser data-channel timeout, ESP32 peer reset,
  failed-peer cleanup, quiet audio receipt, active timeout warnings and dead tracks.
  After the full suite, the reset check was strengthened to use the actual
  close/reinitialize methods; the focused suite passed again.
- Web: `npm --prefix web-client run test -- --run` — **121 passed** across 11 files.
- Web: `npm --prefix web-client run build` — **passed**; existing Browserslist data warning.
- Packaging: `python3 -m build --wheel` — **passed**. Wheel contents and metadata
  verified to include the new transport module and exact Pipecat dependency pin.
- Ruff passes for the new helper, new tests and Wyoming module. Across all touched
  Python files, 11 findings remain in existing LLM/session code; comparison against
  HEAD confirmed they predate this change. No unrelated cleanup was applied.
- `git diff --check` — **passed**.

Residual risks: the data-channel adapter uses private Pipecat hooks and the quiet
policy depends on 1.9.0 audio-track semantics; revalidate both before changing the
pin. Without a firmware warm-wake control event, silent mic resumption cannot be
distinguished from intentional quiet: warnings resume at speech onset or follow-up.
ICE failures, dead tracks and the existing session safety backstop still apply.
Physical ESP32 re-wake, follow-up, barge-in, taper and real Wyoming/OpenClaw turns
need post-deployment observation. No full Docker image build or browser E2E smoke
was performed; component builds and unit/regression suites passed.

Proposed deployment, only after separate authorization: review these changes, build
and tag a candidate image with Pipecat 1.9.0, retain `b45f62f` for rollback, then
update only Vauxr. Verify a living-room cold pre-roll turn through OpenClaw and TTS,
warm-quiet beyond the previous warning interval, wake-word re-wake, follow-up,
barge-in and taper disconnect. Check that the three targeted log issues disappear
while genuine failures remain visible. Roll back to `b45f62f` if voice regresses.
Any eventual PR must target `develop` and request reviewer `lillianama`.
