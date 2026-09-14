# AGENTS.md

## Cursor Cloud specific instructions

### Environment notes
- This repo is a **Python 3.12** project (aiohttp); see `CLAUDE.md` for the module map.
- The base VM has `python3` (no `python` alias) and no `python3-venv`, so dependencies are
  installed into system Python with `pip --break-system-packages` (handled by the update
  script). Use `python3`, not `python`.

### Components & how to run them (dev mode)
- **Backend voice gateway** (`src/`, entry `python3 -m server`): one aiohttp process that
  binds **:8765** (device WebSocket, path `/ws`) and **:8080** (HTTP API `/api/*` + serves the
  built web client). No auth environment variable is required. Owner access requires an exact HTTP/HTTPS owner origin
  and explicit console setup in `docs/authz/owner-v1.md`; `DEVICE_TOKEN` grants no access. Run from the repo root —
  static file serving resolves `web-client/dist` relative to the current working directory.
  Example: `DATA_DIR=/workspace/.data python3 -m server`.
- `DATA_DIR` must be writable (channel registry persists to `<DATA_DIR>/config.json`). Its
  default is `/data` (used by the Docker image); set a writable local path when running outside
  Docker or channel create/rotate will fail.
- **Web client** (`web-client/`, React+Vite): `npm run build` emits `web-client/dist`.
  Serve that build through the backend at the exact configured owner origin. The
  cross-origin Vite development server cannot authenticate owner sessions. The browser
  voice URL uses the same authority and `/ws`, with HTTP/WS by default or optional
  verified HTTPS/WSS. There is no shared-token connection form.

### STT/TTS and realtime (not needed for control-plane work)
- Actual voice turns need **Whisper (STT)** and **Piper (TTS)** Wyoming services on
  `tcp://127.0.0.1:10300` / `:10200`. These come from the `rhasspy/wyoming-*` images via
  `docker compose up` (Docker is **not** preinstalled). The WS + HTTP control plane (devices,
  channels, static UI) runs fully without them; only voice turns fail if they're absent.
- The WebRTC **realtime** path is opt-in (`REALTIME_ENABLED=1`, also needs `REALTIME_HOST`) and
  requires the heavy `[realtime]` extra (pipecat/aiortc). It is off by default and its deps are
  not installed by the update script.

### Tests
- Backend: `pytest -q` (config in `pyproject.toml`, `pythonpath=["src"]`).
- Web client: `npm --prefix web-client run test` (Vitest; use `npx vitest run` for one-shot).
- E2E: build the web client, then `npm --prefix e2e test`. The suite starts disposable
  aiohttp servers and clean Chromium contexts; no running backend or shared token is
  required. Install dependencies/browser first as documented in `e2e/README.md`.
