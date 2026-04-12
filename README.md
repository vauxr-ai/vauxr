# Vauxr

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Self-hosted voice gateway for [Vauxr](https://vauxr.ai) — connects voice hardware to OpenClaw (or any LLM backend) with zero cloud dependency, zero port forwarding, and zero sidecar setup.

## What it is

A self-hosted Docker stack: voice gateway server + pluggable STT and TTS backends. One `docker compose up` and your voice device has a full AI voice assistant pipeline.

The default stack ships with [Wyoming](https://github.com/rhasspy/wyoming)-compatible STT and TTS — no API keys, no cloud required. Wyoming is an open protocol for local AI voice services; drop in any compatible provider as your needs change.

## How it works

```
Device (mic) → vauxr → Whisper (STT) → OpenClaw (LLM) → Piper (TTS) → Device (speaker)
```

Any device that speaks the Vauxr WS protocol can connect.

The HTTP API (`/api/devices`) lets your OpenClaw agent push announcements to devices and send control commands without a voice turn.

## Quick Start

1. Clone the repo and copy the example env file:

```bash
git clone https://github.com/vauxr-ai/vauxr.git
cd vauxr
cp .env.example .env
```

2. Edit `.env` with your values:

```env
OPENCLAW_URL=wss://your-openclaw:18789
OPENCLAW_TOKEN=your-token
DEVICE_TOKEN=your-device-shared-secret
```

3. Start the stack:

```bash
docker compose up -d
```

Devices connect to `ws://your-server-ip:8765`. HTTP API at `http://your-server-ip:8080`.

## HTTP API

All endpoints require `Authorization: Bearer <DEVICE_TOKEN>`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/devices` | List connected devices and state |
| `POST` | `/api/devices/{id}/announce` | Push TTS announcement to a device |
| `POST` | `/api/devices/{id}/command` | Send control command (`set_volume`, `mute`, `unmute`, `reboot`) |

A Postman collection is included at `postman/vauxr.postman_collection.json`.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design, protocol spec, and roadmap.

## Related

- [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) — OpenClaw plugin: exposes the HTTP API as agent tools so your OpenClaw agent can announce and control devices automatically
- [vauxr-assistant](https://github.com/lillianama/vauxr-assistant) — Reference firmware for ESP32-S3 voice devices
- [vauxr.ai](https://vauxr.ai) — Hosted cloud version (coming soon)

## License

Copyright © 2026 Lillian Mikus

Vauxr is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of this software as a network service, you must make your source available under the same license.
