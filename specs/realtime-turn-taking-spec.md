# Realtime Turn-Taking & Transport Coordination — Server (vauxr)

**Status:** Draft — design approval requested
**Author:** Lillian + Nova
**Date:** 2026-06-09
**Branch:** `spec/realtime-turn-taking`
**Related (firmware `vauxr-assistant` repo):** `.specify/specs/006-realtime-turn-taking/spec.md` on the same branch name

This is the server counterpart to the firmware spec. **Decision locked:** vauxr WS stays the device server + the turn-based pipeline; pipecat is used **only** as the WebRTC realtime engine. No pipecat WS transport, no shared-pipeline rewrite.

---

## Problem (server view)

Today `_flush_preroll` seeds the first realtime turn when the device sends `realtime.media_ready` — i.e. when WebRTC finishes negotiating. Because that timing is decoupled from when the user actually stops talking, the seeded "first utterance" is whatever audio happened to be buffered (partial or empty), and any continuation is then handed to server-side Silero VAD mid-sentence. Result: premature/partial first turns or turns that never end. See firmware spec for the full race.

Additionally, the server tears the Pipecat session down eagerly on `follow_up:false`, forcing re-negotiation on the next wake.

## Goals

1. Seed/process a **cold-start** turn on a **device-VAD end-of-speech marker**, not on `media_ready`.
2. Branch processing **per turn** along two dimensions — *was a peer warm at wake?* and (for cold wakes) *did WebRTC connect by end-of-speech?*:
   - **Warm at wake** (peer already up): no device-VAD marker. Audio arrives on the WebRTC track and **Pipecat's Silero VAD end-points the turn directly** — no transcription seeding.
   - **Cold at wake, still not connected at device end-of-speech:** run the **existing WS turn-based pipeline** (server STT → channel/LLM → TTS over WS).
   - **Cold at wake, but WebRTC connected by device end-of-speech:** the full utterance was buffered over WS, so **transcribe it and seed that text into Pipecat** (LLM via channel → TTS over the WebRTC track). This is the one-time cold→warm handoff — the *only* text-seeding case.
3. Once a peer is connected — whether first connect or a **re-wake on a still-warm peer** — keep using **Pipecat's server-side Silero VAD** for turn-taking + barge-in (Option Y). A wake word on a warm peer is just "resume streaming"; the server does not expect a device-VAD marker for it.
4. Keep Pipecat's `LLMContext` **pristine and complete** across transport switches, for future context-using plugins (ElevenLabs `previous_text`/`next_text`, context-aware LLMs).
5. **No server-driven teardown** — drop the eager close on `follow_up:false`; the device owns the warm idle taper and the server only does **reactive cleanup** when the peer closes (with an optional long safety backstop).

## Non-Goals

- No pipecat WS transport / custom serializer.
- No `openclaw-direct` (operator) support in realtime (unchanged — channel-routed only).
- No change to how the WS turn pipeline itself transcribes/streams (we reuse it as-is for the WS branch).

---

## Design

### 1. Device-VAD boundary drives seeding (cold start only)

Stop triggering the first turn on `realtime.media_ready`. Instead, **when WebRTC was cold at wake**, the device sends `voice.end` after its VAD detects end of speech (the existing turn-boundary marker — no new message type), stamped with **`webrtc_connected: bool`** so the server knows which branch to take (§2). The server has the full utterance buffered (the device streamed it as `0x01` frames over WS). On that signal:

- Transcribe the buffered PCM (batch Whisper, as `_flush_preroll` does today), and
- route the turn per the branch below.

`media_ready` becomes purely a transport-state signal ("device is now streaming on the WebRTC track"), not a turn trigger.

**Warm-peer re-wake:** if a peer is already connected at wake (re-wake inside the warm window), there is **no** device-VAD marker. The device just resumes streaming on the WebRTC track and the server end-points via Silero like any other realtime turn. The server must therefore not assume every wake is preceded by a buffered-WS utterance — only cold waits.

### 2. Per-turn processing branch

Three cases, decided by the device-reported WebRTC state. The device stamps its own `esp_peer` connection state onto the `voice.end` marker as **`webrtc_connected: bool`** — the server cannot infer this from `realtime.media_ready`, because in the cold path the device is still streaming WS pre-roll (single mic reader) and hasn't sent `media_ready` yet:

- **WS-only turn** (cold wake, WebRTC still not connected at end-of-speech): feed the captured utterance through the **existing turn-based pipeline** (`pipeline.py`), response audio framed back over WS. This is the proven path and the graceful-degradation fallback.
- **Text-seeded realtime turn** (cold wake, but WebRTC connected by end-of-speech): `transcribe` the WS-buffered utterance → add user message to `LLMContext` → `LLMRunFrame`, exactly like the current pre-roll seed, with TTS out over the WebRTC track. This is a one-time cold→warm handoff per session.
- **Live realtime turn** (warm peer at wake — first-connect steady state *or* re-wake in the warm window): audio is on the WebRTC track; **Pipecat's Silero VAD end-points it** and the user-aggregator builds `LLMContext` directly. No device marker, no text-seeding.

After the first turn has been served over the WebRTC track, the session is **fully realtime** and stays that way as long as the peer is warm: the device streams continuously and Pipecat's Silero VAD (already configured in `LLMUserAggregatorParams`) end-points subsequent turns, including those that follow a warm-window re-wake.

### 3. Pristine `LLMContext` via a transport-agnostic conversation log

Two distinct notions of history:

- **OpenClaw session** = source of truth for the LLM. Both WS and realtime route through the channel plugin to the same OpenClaw session, so the LLM always sees full history. (Already true.)
- **Pipecat `LLMContext`** = a local copy inside the realtime pipeline. Must be complete/clean for context-using plugins.

Mechanism:

1. Maintain a **per-device conversation log** (`[{role, content}, …]`) on the server, written through a single `record_turn(device_id, user_text, assistant_text)` choke point, called on **every** completed turn — WS or realtime.
2. When a Pipecat realtime session is created, **reconstruct `LLMContext` from the log** so it starts complete regardless of how many turns happened over WS first.
3. During a live realtime session, let Pipecat's aggregators own `LLMContext` and **mirror** completed turns into the log (from `_on_turn_complete`); the log is only ever *read back into* `LLMContext` at session start — never cross-fed mid-session (avoids double-counting).

**Content hygiene** (matters for prosody-conditioning plugins): assistant messages stored with `[[follow_up]]`/control tags **stripped** (spoken text only); user messages stored as the final transcript (not partial/VAD-refinalized fragments); correct role alternation across transports.

### 4. Teardown is reactive cleanup, not a server-driven lifecycle

The server does **not** run the taper clock or decide when the conversation is over — the device owns that (§D of the firmware spec). The server's only job is to **free resources when the peer actually goes away**, and to **not** tear down before then. Concretely:

- **Drop the eager `follow_up:false → manager.stop()`.** `follow_up:false` is not a teardown signal — keep the Pipecat session + `LLMContext` alive through Warm-quiet so a re-wake resumes instantly. Warm-quiet (device paused mic) needs **no** server action beyond *not* closing.
- **Clean up on peer close (event-driven).** When the device drops the peer (taper Drop) the WebRTC connection closes; aiortc's disconnect/closed event drives cleanup (already wired via `_on_disconnected`). An explicit `realtime.stop` over WS is a clean, fast equivalent signal — but the peer-close event alone is sufficient. So there's no server "teardown timer" in the normal path.
- **Safety backstop only.** A server-side timeout is justified *only* to reap a leaked session (device lost power with no clean close) — and even that overlaps with WebRTC's own ICE consent-freshness/disconnect detection, so it can be a long, coarse last resort rather than part of the lifecycle. (Open: whether we keep an explicit backstop at all or rely on aiortc's disconnect.)
- **No ghost turns.** Independently of teardown: never synthesize/seed into a peer that isn't live. Require a live connection (or the WS fallback) before producing audio for a turn. This also retires the old 10 s/15 s seed-timeout asymmetry — seeding is gated on the device-VAD marker + a live peer, not on a race between two timers.

### 5. End-of-turn quality (optional follow-on)

If steady-state Silero end-pointing is still flaky once the cold-start race is removed, evaluate Pipecat **`smart_turn`** (`pipecat.audio.turn.smart_turn`, ML end-of-turn) as a replacement/complement to silence-based VAD for the fully-realtime phase. Out of scope for the first cut; noted so VAD params and smart-turn share one home (per-device settings).

---

## Touch points (server)

- `realtime_session.py` — replace `media_ready`-triggered seeding with device-VAD-marker seeding; per-turn branch; `LLMContext` backfill from the log; drop the eager `follow_up:false` close, keep cleanup reactive on peer close (`_on_disconnected`) / `realtime.stop`.
- `realtime_manager` (in `realtime_session.py`) — per-device conversation log; `record_turn`; keep session alive through Warm-quiet; reap only on peer close / `realtime.stop` (optional long backstop).
- `server.py` — route the device end-of-utterance marker and WebRTC-state to the per-turn branch; WS-only turns continue to use `pipeline.py`.
- `pipeline.py` — call `record_turn` on WS-turn completion (so the log/`LLMContext` stays complete).
- Per-device settings (`device_settings.py`) — house VAD params + taper timers + (later) smart-turn toggle alongside the segmentation flags, keyed by `device_id`.

## Acceptance criteria

- First turn after wake is seeded on **device end-of-speech**, captured in full, processed once — independent of WebRTC negotiation timing.
- WS-only branch produces a correct turn when WebRTC isn't connected; realtime branch streams TTS over WebRTC when it is.
- Fully-realtime follow-ups end-point via server Silero with working barge-in.
- A new Pipecat session created after N WS turns starts with a **complete, clean `LLMContext`** (verified: messages present, tags stripped, roles ordered).
- No "ghost turns": the server never synthesizes into a disconnected peer; device `realtime.stop` always pre-empts server seeding.
- Conversation continuity holds across WS↔WebRTC switches (OpenClaw session + local log agree).

## Decisions / open questions

- **Decided — Warm-quiet resume requires the wake word.** While the model returns `follow_up:true` the session stays in active open-mic follow-up; on `follow_up:false` the turn-flow ends and the device drops to Warm-quiet (peer warm), restarting only on a wake word. The server keeps the session/`LLMContext` alive across that gap (cooperative teardown, §4) and does **not** expect a device-VAD marker on the warm re-wake.
- **Decided — idle-taper timers live in per-device settings, hardcoded for now** (`device_settings.py`, keyed by `device_id`, alongside the segmentation flags; later the devices API). The server is the source of truth and hands them to the device (`hello`/realtime policy). The server's own safety timeout must stay **longer** than the device's drop timer.
- **Noted — server VAD reliability:** if steady-state Silero stays flaky after the cold-start race is fixed, evaluate `smart_turn` (§5).
- **Decided — reuse `voice.end`** as the device end-of-utterance marker (no new `realtime.utterance_end`). The device already sends `voice.end` for turn boundaries; the server treats `voice.end` during a cold realtime wait as the cue to transcribe-and-route (per §1/§2).
- **Decided — WS-only branch keeps streaming STT (`stt_stream`).** No change: the existing turn-based pipeline already streams for low latency, and the WS-only branch reuses it as-is. (Batch Whisper stays only on the cold→warm *seed* path in `_flush_preroll`, where a complete buffered utterance is transcribed once.)
