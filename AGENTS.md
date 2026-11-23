# AGENTS.md

## Cursor Cloud specific instructions

### Environment notes
- This repo is a **Python 3.12** project (aiohttp); see the [maintainer module map](ARCHITECTURE.md#runtime-boundaries).
- The base VM has `python3` (no `python` alias) and no `python3-venv`, so dependencies are
  installed into system Python with `pip --break-system-packages` (handled by the update
  script). Use `python3`, not `python`.

### Components & how to run them (dev mode)
- **Backend voice gateway** (`src/`, entry `python3 -m vauxr`): one aiohttp process that
  binds **:8765** (device WebSocket, path `/ws`) and **:8080** (HTTP API `/api/*` + serves the
  built web client). No auth environment variable is required. Owner access requires an exact HTTP/HTTPS owner origin
  and explicit console setup in `docs/authz/owner-v1.md`; `DEVICE_TOKEN` grants no access. Run from the repo root —
  static file serving resolves `web-client/dist` relative to the current working directory.
  Example: `python3 -m vauxr` after building the client.
- `DATA_DIR` must be writable. Server settings persist to `config.json`, routing metadata
  to `agents.json`, and credential authority to `authz.json`. Its native default is
  `./data` relative to the working directory; Compose sets `/data` in the container.
- **Web client** (`web-client/`, React+Vite): `npm run build` emits `web-client/dist`.
  Serve that build through the backend at the exact configured owner origin. The
  cross-origin Vite development server cannot authenticate owner sessions. The browser
  voice URL uses the same authority and `/ws`, with HTTP/WS by default or optional
  verified HTTPS/WSS. There is no shared-token connection form.

### STT/TTS and realtime (not needed for control-plane work)
- Standard voice turns need Wyoming STT/TTS services; the native defaults are
  `tcp://127.0.0.1:10300` / `:10200`. Compose offers optional Whisper/Piper images via
  `--profile local-speech` (Docker is **not** preinstalled in the base VM). The WS + HTTP
  control plane runs without speech services; voice turns fail if they're absent.
- WebRTC is enabled by default for native runs and needs the `[realtime]` extra
  (pipecat/aiortc). The update script does not install that extra; set
  `REALTIME_ENABLED=0` for control-plane-only development in that VM. Native
  installs infer the ICE host from the trusted owner origin when local; a proxy
  origin may need an explicit `REALTIME_HOST`.

### Tests
- Backend: `pytest -q` (config in `pyproject.toml`, `pythonpath=["src"]`).
- Web client: `npm --prefix web-client run test` (Vitest; use `npx vitest run` for one-shot).
- E2E: build the web client, then `npm --prefix e2e test`. The suite starts disposable
  aiohttp servers and clean Chromium contexts; no running backend or shared token is
  required. Install dependencies/browser first as documented in `e2e/README.md`.

## Shared contributor guidance

Read [ARCHITECTURE.md](ARCHITECTURE.md) for system boundaries and protocols and
[ROADMAP.md](ROADMAP.md) for planned work before coding. Vauxr is a self-hosted
asyncio/aiohttp voice gateway connecting devices to STT, LLM and TTS backends.

### Package layout

The package lives in `src/vauxr/`. Entrypoints are `vauxr.server:main` (the
`vauxr` CLI) and `python3 -m vauxr`. Tests import package source, not legacy
flat `src/*.py` modules.

### Git workflow

- Start new branches from current `develop`; preserve any existing worktree changes.
- Use descriptive feature branches such as `feat/short-description`.
- Open PRs into `develop`, never directly into `main`; request `lillianama` as reviewer.
- Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`.

### Implementation rules

- Fully type-hint Python; avoid `Any`. Keep `ruff` clean (line length 110).
- Use `aiohttp`, not another web framework; WebSocket and HTTP APIs share one app.
- Reuse `synthesize()` from `vauxr.speech.wyoming_tts` for TTS.
- Reuse `make_binary_frame` from `vauxr.utils` and `next_seq` from
  `vauxr.devices.registry` for binary WebSocket frames.
- Keep STT, LLM and TTS stages clearly separated in `src/vauxr/pipeline.py`.
- Never commit credentials or tokens.
