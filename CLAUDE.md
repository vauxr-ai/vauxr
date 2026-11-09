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

The package is `src/vauxr/`; see the [maintainer module map](ARCHITECTURE.md#runtime-boundaries)
for current module ownership. Entrypoints are `vauxr.server:main` (`vauxr` CLI)
and `python3 -m vauxr`. Tests import the installed/package source, not legacy
flat `src/*.py` modules.

Run locally with `python3 -m vauxr` and a writable `DATA_DIR`; no auth token is
required at startup. Owner HTTP/HTTPS setup and console commands are documented in
`docs/authz/owner-v1.md`. Legacy `DEVICE_TOKEN` grants no access. Tests run under `pytest`.

## Git Workflow

- **Always** branch from `develop` (`git checkout develop && git pull`)
- Branch naming: `feat/short-description`
- PR back into `develop` — never directly into `main`
- Reviewer: `lillianama`
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`

## Key Rules

- Fully type-hinted Python — annotate everything, avoid `Any`; keep `ruff` clean (line-length 110)
- No web framework beyond `aiohttp` — the WS server and HTTP API share one `aiohttp` app
- Reuse `synthesize()` from `vauxr.speech.wyoming_tts` for all TTS (no duplicate callers)
- Reuse `make_binary_frame` (`vauxr.utils`) / `next_seq` (`vauxr.devices.registry`) for all binary WS frames
- Keep pipeline stages (STT, LLM, TTS) clearly separated in `pipeline.py`
- No credentials or tokens in commits
