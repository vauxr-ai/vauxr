# vauxr — Claude Code Context

This file is read by Claude Code at the start of every session. Read it before doing anything else.

## What This Is

`vauxr` is a self-hosted Docker stack that gives any Vauxr voice device a full STT → LLM → TTS pipeline. It bridges hardware (via the Vauxr WS protocol) to OpenClaw.

**Stack:** Python `aiohttp` WebSocket + HTTP bridge server + Whisper (STT) + Piper (TTS), all via Wyoming protocol
**Language:** Python 3.12 (asyncio, `aiohttp`)

## Must-Read Before Coding

1. **`ARCHITECTURE.md`** — full system diagram, protocol spec, component descriptions.
2. **`ROADMAP.md`** — planned features by theme.

## File Structure

```
src/
├── server.py          # WS server — device + channel connections, message routing
├── pipeline.py        # Voice turn pipeline: STT → LLM → TTS → audio
├── openclaw_client.py # OpenClaw native WS protocol client
├── http_server.py     # HTTP API server (device + channel endpoints) + static web client
├── channel_registry.py # Persisted routing-channel store (config.json under DATA_DIR)
├── channel_server.py  # Channel WS server (backend response routing)
├── device_registry.py # Connected device registry (+ next_seq helper)
├── device_config.py   # Per-device config validation
├── device_settings.py # Persisted device settings
├── wyoming_stt.py     # Whisper STT via Wyoming protocol
├── wyoming_tts.py     # Piper TTS via Wyoming protocol (synthesize())
├── protocol.py        # WS message encode/parse + binary frame helpers
├── auth.py            # Token validation
├── utils.py           # make_binary_frame + shared helpers
├── config.py          # Config from env vars
└── realtime_*.py      # Opt-in WebRTC (Pipecat) realtime transport
```

Run locally with `python -m server` (needs `DEVICE_TOKEN`); tests run under `pytest`.

## Git Workflow

- **Always** branch from `develop` (`git checkout develop && git pull`)
- Branch naming: `feat/short-description`
- PR back into `develop` — never directly into `main`
- Reviewer: `lillianama`
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`

## Key Rules

- Fully type-hinted Python — annotate everything, avoid `Any`; keep `ruff` clean (line-length 110)
- No web framework beyond `aiohttp` — the WS server and HTTP API share one `aiohttp` app
- Reuse `synthesize()` from `wyoming_tts.py` for all TTS (no duplicate callers)
- Reuse `make_binary_frame` (`utils.py`) / `next_seq` (`device_registry.py`) for all binary WS frames
- Keep pipeline stages (STT, LLM, TTS) clearly separated in `pipeline.py`
- No credentials or tokens in commits
