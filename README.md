# Vauxr

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

**Vauxr is an open protocol for voice assistants** — a hardware-agnostic, ecosystem-agnostic standard for connecting microphones, speakers, and wake-word devices to any voice backend.

## The Problem

Voice is becoming a natural interface for AI, but today it's locked in:

- **Alexa** only works with Amazon
- **Google Assistant** only works with Google
- **Home Assistant voice** only works with HA's pipeline
- **ESPHome devices** can only talk to HA

Every new LLM, agent platform, or creative application has to reinvent the voice stack from scratch — or give up and plug into someone else's walled garden. That's a problem for innovation.

## The Solution

Vauxr is an open wire protocol any device and any backend can implement — like MQTT for messaging, or USB for peripherals. Speak Vauxr on the device, speak Vauxr on the server, and the pieces compose freely.

- Any hardware (ESPHome, custom firmware, desktop app) can be a Vauxr device
- Any backend (OpenClaw, custom LLM stack, home automation) can be a Vauxr server
- Swap STT, TTS, or LLM providers without re-flashing devices or rewriting integrations

## This repository

This repo is the **reference server implementation** — a self-hosted Docker stack that speaks the Vauxr protocol and ships with [Wyoming](https://github.com/rhasspy/wyoming)-compatible Whisper (STT) and Piper (TTS) out of the box. Use it as-is, or as a blueprint for your own implementation.

## How it works

```
Device (mic) → vauxr → Whisper (STT) → LLM backend → Piper (TTS) → Device (speaker)
```

Any device that speaks the Vauxr WS protocol can connect. The HTTP API (`/api/devices`) lets your backend push announcements to devices and send control commands without a voice turn.

## Quick Start

1. Clone the repo and copy the example env file:

```bash
git clone https://github.com/vauxr-ai/vauxr.git
cd vauxr
cp .env.example .env
```

2. Edit `.env` — only one value required:

```env
DEVICE_TOKEN=your-device-shared-secret
```

3. Start the stack:

```bash
docker compose up -d
```

Devices connect to `ws://your-server-ip:8765`. HTTP API at `http://your-server-ip:8080`.

## Connecting to OpenClaw

The recommended path is the [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) channel plugin, installed from [ClaWHub](https://clawhub.ai):

```bash
openclaw plugins install clawhub:@vauxr/openclaw
```

The plugin wires OpenClaw to your Vauxr server and exposes device announcements and controls as agent tools. See the [vauxr-openclaw README](https://github.com/vauxr-ai/vauxr-openclaw) for configuration.

## Connecting to other backends

Vauxr is backend-agnostic. If you're not using OpenClaw, connect your own LLM or agent service to the Vauxr WS protocol — see [ARCHITECTURE.md](./ARCHITECTURE.md) for the protocol spec.

## HTTP API

All endpoints require `Authorization: Bearer <DEVICE_TOKEN>`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/devices` | List connected devices and state |
| `POST` | `/api/devices/{id}/announce` | Push TTS announcement to a device |
| `POST` | `/api/devices/{id}/command` | Send control command (`set_volume`, `mute`, `unmute`, `reboot`) |

A Postman collection is included at `postman/vauxr.postman_collection.json`.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design and protocol spec, and [ROADMAP.md](./ROADMAP.md) for what's planned.

## Related

- [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) — OpenClaw channel plugin: exposes the HTTP API as agent tools so your OpenClaw agent can announce and control devices automatically
- [vauxr.ai](https://vauxr.ai) — Hosted cloud version (coming soon)

## License

Copyright © 2026 Lillian Mikus

Vauxr is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of this software as a network service, you must make your source available under the same license.
