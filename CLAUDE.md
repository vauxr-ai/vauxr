# vauxr — Claude Code Context

This file is read by Claude Code at the start of every session. Read it before doing anything else.

## What This Is

`vauxr` is a self-hosted Docker stack that gives any Vauxr voice device a full STT → LLM → TTS pipeline. It bridges hardware (via the Vauxr WS protocol) to OpenClaw.

**Stack:** Node.js WebSocket bridge server + Whisper (STT) + Piper (TTS), all via Wyoming protocol
**Language:** TypeScript (ESM)

## Must-Read Before Coding

1. **`ARCHITECTURE.md`** — full system diagram, protocol spec, component descriptions.
2. **`ROADMAP.md`** — planned features by theme.

## File Structure

```
src/
├── server.ts          # WS server — device connections, message routing
├── pipeline.ts        # Voice turn pipeline: STT → LLM → TTS → audio
├── openclaw-client.ts # OpenClaw native WS protocol client
├── http-server.ts     # HTTP API server (device management endpoints)
├── device-registry.ts # Connected device registry
├── wyoming-stt.ts     # Whisper STT via Wyoming protocol
├── wyoming-tts.ts     # Piper TTS via Wyoming protocol
├── auth.ts            # Token validation
└── config.ts          # Config from env vars
```

## Git Workflow

- **Always** branch from `develop` (`git checkout develop && git pull`)
- Branch naming: `feat/short-description`
- PR back into `develop` — never directly into `main`
- Reviewer: `lillianama`
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`

## Key Rules

- Strict TypeScript — no `any`, no implicit returns
- No framework for the HTTP server — plain Node `http` module
- Reuse `synthesize()` from `wyoming-tts.ts` for all TTS (no duplicate callers)
- Reuse `makeBinaryFrame` / `nextSeq` helpers for all binary WS frames
- Keep pipeline stages (STT, LLM, TTS) clearly separated in `pipeline.ts`
- No credentials or tokens in commits
