# Vauxr Python Rewrite — Spec

**Status:** Draft for review
**Author:** Nova + Lillian
**Date:** 2026-05-16

---

## Goal

Port the `vauxr` server (WS bridge + HTTP API) from Node.js/TypeScript to Python. **Same protocol, same behavior, same wire contract.** This is a clean rewrite — no new features, no Pipecat, no scope creep. Pipecat integration and the Pipelines web UI are explicit follow-up phases with their own specs.

---

## Non-Goals (this phase)

- **No Pipecat.** Not even scaffolding for it. That's a follow-up spec.
- **No new web client features.** Existing UI must keep working unchanged.
- **No new HTTP endpoints, no new WS messages, no new env vars.**
- **No firmware changes.** `vauxr_client` is untouched.
- **No changes to `vauxr-openclaw` channel plugin.** It stays in TypeScript and keeps talking to the same `/channel` contract.
- **No changes to OpenClaw.** Same gateway WS protocol consumed identically.
- **No Whisper / Piper replacement.** Wyoming clients only.
- **No refactor of behavior** — even quirks that look weird get preserved in this pass; cleanups come after parity is proven.

---

## Why now

- Codebase is lean (~2,600 lines TS across 16 files in `src/`).
- Protocol is stable enough to clone exactly.
- Pre-Pipecat is the right window — porting after Pipecat lands means porting more surface area.
- Python ecosystem is where Pipecat lives, and where most of the interesting voice tooling is moving. Doing this clean pass first means future integrations don't have to bridge a language gap.

---

## What gets ported (1:1 from `src/`)

| TS file | Python module | Notes |
|---|---|---|
| `server.ts` | `vauxr/server.py` | Entry point; wires WS + HTTP. |
| `config.ts` | `vauxr/config.py` | Same env vars, same defaults. |
| `auth.ts` | `vauxr/auth.py` | Same bearer-token check. |
| `device-identity.ts` | `vauxr/device_identity.py` | Same identity / token logic. |
| `device-registry.ts` | `vauxr/device_registry.py` | Connected device state. |
| `device-config.ts` | `vauxr/device_config.py` | `devices.json` schema preserved. |
| `channel-registry.ts` | `vauxr/channel_registry.py` | Routing mode registry. |
| `channel-server.ts` | `vauxr/channel_server.py` | `/channel` WS for OC plugin. |
| `openclaw-client.ts` | `vauxr/openclaw_client.py` | Outbound mode (gateway WS). |
| `http-server.ts` | `vauxr/http_server.py` | `/api/devices/*` endpoints. |
| `pipeline.ts` | `vauxr/pipeline.py` | Whisper → OC → Piper flow. |
| `wyoming-stt.ts` | `vauxr/wyoming_stt.py` | Wyoming client. |
| `wyoming-tts.ts` | `vauxr/wyoming_tts.py` | Wyoming client. |
| `idle-segmenter.ts` | `vauxr/idle_segmenter.py` | Idle-pause text flush. |
| `segment-queue.ts` | `vauxr/segment_queue.py` | TTS segment queueing. |
| `utils.ts` | `vauxr/utils.py` | Shared helpers. |

Same module boundaries, same names (snake_case). Future structural changes happen after parity is proven, not during the port.

---

## Compatibility Contract — must not change

- **Vauxr WS protocol** — every JSON control message (`voice.start`, `voice.end`, `abort`, `ready`, `transcript`, `audio.end`, `device.control`, `error`) and every binary frame type (`0x01`, `0x02`, `0x03`) behaves identically. 3-byte header preserved.
- **HTTP API** — `GET /api/devices`, `POST /api/devices/{id}/announce`, `POST /api/devices/{id}/command`. Same bodies, same responses, same auth header.
- **`/channel` WS** — exact same contract; existing `vauxr-openclaw` plugin must connect without any change.
- **OpenClaw gateway WS** — `chat.send`, `chat` event subscription, persistent session key `vauxr:${device_id}`.
- **Wyoming** — same protocol calls to faster-whisper and Piper.
- **Env vars** — every existing one preserved (`DEVICE_TOKEN`, `OPENCLAW_URL`, `OPENCLAW_TOKEN`, `STREAMING_TTS_IDLE_PAUSE_MS`, port configs, etc.). No new ones.
- **Disk state** — `devices.json` schema unchanged; loadable round-trip with the old version.
- **Docker** — `docker compose up -d` from the same `.env.example` produces a working stack.

---

## Tech stack

- **Python 3.12+**
- **`aiohttp`** — single async server for HTTP + WS (matches current Node single-process pattern)
- **`wyoming`** — official Wyoming protocol client
- **`pydantic`** v2 — config + message validation
- **`uvloop`** — asyncio perf boost
- **`pytest` + `pytest-asyncio`** — tests

Avoid: heavy frameworks, ORMs, anything not strictly needed for parity.

---

## Source layout

```
vauxr/
  pyproject.toml
  Dockerfile                  (Python image — replaces Node build)
  docker-compose.yml          (updated to build new image; Whisper/Piper services unchanged)
  src/vauxr/
    __init__.py
    server.py
    config.py
    auth.py
    device_identity.py
    device_registry.py
    device_config.py
    channel_registry.py
    channel_server.py
    openclaw_client.py
    http_server.py
    pipeline.py
    wyoming_stt.py
    wyoming_tts.py
    idle_segmenter.py
    segment_queue.py
    utils.py
  web-client/                 (unchanged)
  tests/
    test_protocol_parsing.py
    test_pipeline_e2e.py
    test_http_api.py
    test_channel_ws.py
    test_announce.py
    test_device_control.py
```

The Node source under `src/` stays in the repo on a `legacy/node` branch (or moved to `legacy-node/` directory) during the parallel-run window — see "Cutover" below.

---

## Build plan — sequential

Each step ends with the suite green and the system runnable end-to-end. No step bundles unrelated work.

1. **Scaffold** — `pyproject.toml`, Python `Dockerfile`, `docker-compose.yml` swap, empty entry point, CI runs `pytest`.
2. **Config + auth + utils** — ports `config.ts`, `auth.ts`, `utils.ts`. Unit tests for env-var loading and token check.
3. **Protocol parsing** — frame parser (JSON vs binary discrimination, 3-byte audio header). Pure-function module + tests, no I/O.
4. **Device WS server (handshake only)** — accept connections, validate auth, emit `ready`. No voice flow yet. Integration test with a fake client.
5. **Device registry + identity + config** — ports `device-identity.ts`, `device-registry.ts`, `device-config.ts`. Round-trip test against an existing `devices.json`.
6. **HTTP API skeleton** — `GET /api/devices` only. Same shape as TS response. Auth check shared with WS.
7. **Wyoming STT client** — port `wyoming-stt.ts`. Stream PCM in, transcript out. Test against the existing Whisper container.
8. **Wyoming TTS client** — port `wyoming-tts.ts`. Text in, PCM frames out. Test against the existing Piper container.
9. **OpenClaw client** — port `openclaw-client.ts`. Connect to gateway WS, send `chat.send`, subscribe to `chat` events. Test against Lillian's existing OpenClaw.
10. **Idle segmenter + segment queue** — port `idle-segmenter.ts` and `segment-queue.ts`. Unit tests for the timing behavior — this is the only behavior that's both subtle and easy to break.
11. **Pipeline (full voice turn)** — port `pipeline.ts`. End-to-end test: simulated device → audio → Whisper → OpenClaw → Piper → audio back to device.
12. **Channel registry + channel server** — port `channel-registry.ts` and `channel-server.ts`. Test against the existing `vauxr-openclaw` plugin in inbound mode.
13. **HTTP announce + device control** — port the `/announce` and `/command` endpoints. Real-device test.
14. **Parity sweep** — full manual run-through against Waveshare and Satellite 1. Whatever was working in the Node version works here.

---

## Test strategy

- **Unit tests** — protocol parsing, idle segmenter, config loading, frame round-trips. No I/O.
- **Integration tests** — Wyoming clients against the real containers (docker-compose harness in CI).
- **End-to-end tests** — fake device client driving full voice turns through the stack.
- **Parity tests** — same canned audio file in both Node and Python, compare transcripts and ensure response routing matches.
- **Plugin compat test** — boot Python vauxr, point existing `vauxr-openclaw` plugin at it, run a full chat turn.

---

## Cutover

1. **Phases 1–11 in a feature branch.** Python image built alongside, not replacing, the Node image.
2. **Parallel run window.** Once Phase 11 is green, point Lillian's local stack at the Python image while keeping the Node image one config flip away. Run on real hardware for a few days.
3. **Hard cutover** — once parallel run is clean: bump version, drop the Node `src/` (move to `legacy-node/` for one release cycle for reference, then delete), publish Python Docker image as the new default.
4. **Tag** the last Node release before the cutover so anyone pinning to it stays unaffected.

---

## Open questions for Lillian

1. **Single Python service vs split** — recommend single process matching current Node pattern. Confirm?
2. **`pyproject.toml` packaging** — keep as a published package on PyPI? Or stay container-only for now?
3. **CI** — current repo uses GitHub Actions for Docker publish. Add `pytest` job there, or separate workflow?
4. **Legacy Node retention** — move to `legacy-node/` for one release, or branch-only?
5. **Whisper / Piper images** — current `docker-compose.yml` brings them up. Any version pins to preserve, or fine to take the same images as-is?
6. **`devices.json` location** — same path as current Node version, or move to a clearer location during the rewrite (e.g. `/data/devices.json`)? Recommend: same path, no movement during port.
7. **Logging format** — current Node logger has a specific shape (likely JSON or pino-style). Worth matching, or fine to use Python's stdlib `logging` with a similar JSON formatter?
8. **Version bump** — major (semver-signalling internal rewrite) or minor (no public contract change)? Recommend: minor + clear release notes, since the contract is unchanged.

---

## Risk notes

- **Wyoming streaming TTS parity** — verify the Python `wyoming` package can drive Piper's chunked output at the same cadence the Node version achieves. Idle-pause flushing is timing-sensitive and the most likely place to see drift.
- **OpenClaw WS edge cases** — reconnect logic, half-open detection, and the per-device session key behavior need explicit tests against a live OpenClaw to catch subtle differences.
- **Binary frame backpressure** — Python WS libraries handle backpressure differently than Node `ws`. Confirm large TTS streams don't stall under `aiohttp`.
- **Plugin compat** — `vauxr-openclaw` plugin is the canary; if it works untouched against the Python `/channel` server, the port is good.

---

## Success criteria

- Existing devices, OpenClaw, and `vauxr-openclaw` plugin all work identically with zero config changes.
- All env vars and on-disk state files load round-trip.
- Voice turn latency within 10% of the Node version (measured end-to-end, audio in to audio out).
- Test suite ≥ parity for everything covered in the Node repo, plus new tests for behaviors that weren't previously covered (idle segmenter timing, frame parsing edge cases).
- Total Python source under ~3,000 lines.
