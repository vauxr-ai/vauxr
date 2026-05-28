# WebRTC Transport PoC — Server / Pipecat Spec

**Status:** Draft for review  
**Author:** Lillian + Nova  
**Date:** 2026-05-27  
**Branch:** `feat/webrtc-transport-poc`  
**Related (private firmware repo):** Spec 005 at `.specify/specs/005-webrtc-transport-poc/spec.md` — Satellite1 firmware (same branch name; not linked here)

---

## Goal

Provide a **standalone server-side PoC** so Satellite1 firmware can test WebRTC voice (barge-in, latency, turn-taking) against the **same Wyoming Whisper/Piper/OpenClaw stack** — without modifying production `pipeline.py` or the Vauxr WS protocol until the feel-test completes.

**Deliverable for this PR:** spec approval + optional `poc/pipecat-realtime/` scaffold (no production integration).

Implementation on the same branch continues after spec approval. Firmware wire contract (RTVI JSON, etc.) is duplicated in this doc where needed — no dependency on the private repo.

---

## Non-Goals (this phase)

- **No changes to `/ws` device protocol** or `pipeline.py` default path.
- **No `transport.switch` in production server** — spike only.
- **No firmware in this repo** — firmware spike is specified separately (private repo).
- **No pipecat-esp32** on device; ESP32 uses our own `esp_peer` integration.
- **No LiveKit** — future transport; not in PoC.
- **No Docker compose changes** for production `vauxr` service (PoC runs standalone on `:7860`).

---

## PoC layout

```
poc/pipecat-realtime/
├── README.md
├── bot.py                 # Pipecat interruptible pipeline + dev runner
├── wyoming_services.py    # STT/TTS → existing whisper/piper containers
├── pyproject.toml
└── .env.example
```

Runs **alongside** production vauxr (`:8765/ws`), not inside it:

```bash
docker compose up -d whisper piper   # same containers as production
cd poc/pipecat-realtime && python bot.py -t webrtc --esp32 --host 0.0.0.0
```

---

## Pipeline (PoC bot)

```
WebRTC (SmallWebRTC) in/out
  → Wyoming Whisper STT (TCP, streaming segments via Silero VAD)
  → OpenAI-compatible LLM (OPENAI_API_KEY or OpenClaw LLM_BASE_URL)
  → Wyoming Piper TTS (TCP)
```

Reuses docker-compose **whisper** and **piper** services — same voices as production.

---

## ESP32 signaling

| Item | Value |
|---|---|
| Endpoint | `POST /api/offer` |
| ESP32 mode | `--esp32 --host <lan-ip>` on bot runner |
| SDP | Pipecat `SmallWebRTCRequestHandler` with `esp32_mode=True` |

Firmware posts `{ "type": "offer", "sdp": "..." }`, receives answer SDP.

---

## RTVI datachannel

Pipecat `PipelineTask` enables RTVI by default. Firmware must send `client-ready` on datachannel `rtvi-ai`:

```json
{
  "label": "rtvi-ai",
  "type": "client-ready",
  "id": "1",
  "data": {
    "version": "1.3.0",
    "about": {
      "library": "vauxr-assistant",
      "library_version": "0.1.0",
      "platform": "esp32-s3"
    }
  }
}
```

### Optional: `--no-rtvi` flag (Phase 2 bring-up)

Add CLI flag to `bot.py` that sets `PipelineTask(enable_rtvi=False)` so firmware can validate **audio-only** WebRTC before implementing RTVI. Document in README; remove or keep based on spike needs.

---

## Config (`.env`)

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` or `LLM_BASE_URL` + `LLM_API_KEY` | LLM |
| `WHISPER_URL` | `tcp://127.0.0.1:10300` |
| `PIPER_URL` | `tcp://127.0.0.1:10200` |
| `PIPER_VOICE` | Match production default |

---

## Spike phases (server — after spec approval)

### Phase A — Browser feel-test

- Run PoC bot; open `http://<host>:7860/client`.
- Validate Wyoming + LLM + Piper path before hardware.

### Phase B — ESP32 Phase 1 (firmware spike)

- `--esp32 --host <ip>`; firmware handshake only.

### Phase C — ESP32 Phase 2–3

- Full audio + RTVI; side-by-side with production WS on `:8765`.

---

## Future production path (out of scope — document intent only)

If feel-test succeeds:

1. **Hybrid transport** — WS remains default; server sends `transport.switch` to upgrade media to WebRTC during TTS/barge-in window (separate spec).
2. **Pipecat module in vauxr** — `src/realtime/` or optional service, not replacing `pipeline.py`.
3. **LiveKit** — swap SmallWebRTC transport in Pipecat; firmware swaps `esp_peer` signaling URL / LiveKit token flow.

Production WS protocol and docker default entrypoint **unchanged** unless explicitly cut over later.

---

## Success criteria

- [ ] PoC bot runs from `poc/pipecat-realtime/` against docker whisper/piper.
- [ ] Browser client can hold interruptible conversation on LAN.
- [ ] `--esp32` mode accepts Satellite1 SDP from the firmware spike.
- [ ] No regressions to production `vauxr` service (PoC is isolated).

---

## Open decisions

1. Commit `poc/pipecat-realtime/` in this PR or only after spec approval? *(This PR: spec + PoC scaffold OK if reviewer prefers runnable server.)*
2. Pin Pipecat version in `pyproject.toml` for reproducibility.
3. Whether to add `--no-rtvi` before or during firmware Phase 2.

---

## References

- [Pipecat SmallWebRTC](https://docs.pipecat.ai/server/services/transport/small-webrtc)
- [RTVI protocol](https://docs.pipecat.ai/client/rtvi/introduction) (client-ready message)
- Production pipeline: `src/pipeline.py` (unchanged in PoC)
