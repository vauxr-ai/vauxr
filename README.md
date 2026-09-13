# Vauxr

![Open Protocol](https://img.shields.io/badge/protocol-Vauxr_1.0-8B5CF6?style=flat-square)
![Docker Build](https://img.shields.io/github/actions/workflow/status/vauxr-ai/vauxr/publish.yml?branch=main&style=flat-square&label=docker%20build&color=8B5CF6)
![Docker Pulls](https://img.shields.io/docker/pulls/vauxr/vauxr?style=flat-square&logo=docker&color=8B5CF6)
![Latest Release](https://img.shields.io/github/v/release/vauxr-ai/vauxr?style=flat-square&include_prereleases&color=8B5CF6)
![Last Commit](https://img.shields.io/github/last-commit/vauxr-ai/vauxr/develop?style=flat-square&color=8B5CF6)

**Vauxr is an open source self-hostable voice assistant platform** — it comes out-of-the-box with a fast, local voice pipeline with idle-pause detection and follow-up mode. Great for talking to your OpenClaw agent.

This repo comes pre-configured as a Docker stack that ships with [Wyoming](https://github.com/rhasspy/wyoming)-compatible Whisper (STT) and Piper (TTS) out of the box. Use it as-is, or as a blueprint for your own implementation.

Speech selection also supports operator-configured Wyoming STT/TTS deployments
with opaque model and voice IDs. Use `STT_URL`, `TTS_URL`, and `TTS_VOICE` for
server defaults (legacy environment names remain supported), or configure a
catalog for global/device selections. See [speech settings](docs/speech-settings.md)
for precedence, examples, and protocol limitations.

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

3. Prepare the persistent directory and start the stack:

```bash
mkdir -p data/piper data/whisper
docker compose build vauxr
docker compose run --rm --no-deps --user 0 vauxr \
  sh -c 'chown 100:101 /data && chmod 700 /data'
docker compose up -d
```

The initialization runs through the selected Docker daemon so ownership works
with both rootful and rootless Docker. Vauxr runs as container UID 100/GID 101;
do not use host `chown 100:101` for a rootless deployment.

Use the web client or HTTP API at `http://your-server-ip:8080`. Voice devices connect to `ws://your-server-ip:8765`.

## Connecting to OpenClaw

The recommended path is the [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) channel plugin, installed from [ClaWHub](https://clawhub.ai):

```bash
openclaw plugins install clawhub:@vauxr/openclaw
```

The plugin wires OpenClaw to your Vauxr server and exposes device announcements and controls as agent tools. See the [vauxr-openclaw README](https://github.com/vauxr-ai/vauxr-openclaw) for configuration.

## Persistent data

Vauxr bind-mounts `./data` beside this Compose file into `/data`. This directory
holds device settings (`devices.json`), webhooks (`webhooks.json`), channels
(`channels.json`), routing (`config.json`), and the direct-connection identity
(`vauxr-identity.json`) when those features are used. It is ignored by Git;
back it up securely because it can contain credentials and private keys.
Recordings and firmware retain their separate `./recordings` and `./firmware`
mounts. Whisper and Piper bind-mount `./data/whisper` and `./data/piper`,
respectively, into their own `/data` directories for model caches.

### Migrating existing installations

Migration is only needed when changing existing named volumes to the new bind mounts—not when updating the Docker image alone. Copy Vauxr's data into `./data/`, Piper's cache into `./data/piper/`, and Whisper's cache into `./data/whisper/`. Keep the old volumes and a backup until everything works; never use `docker compose down -v` during migration.

1. **Find the old volumes.** Inspect the services' `/data` mounts with `docker inspect vauxr piper whisper`. If containers were already recreated, use `docker volume ls` and your previous configuration to identify the sources; don't guess between `vauxr_*` and `vauxr-local_*`.
2. **Stop the services.** Run `docker compose stop vauxr piper whisper`, and stop any other containers sharing those volumes or data directories.
3. **Back up and copy.** Back up any existing `./data/`, then copy the selected sources into a fresh directory with the layout above, preserving ownership and permissions. Leave the separate `./firmware/` and `./recordings/` mounts unchanged. Check that settings and model files copied successfully before replacing `./data/` with the prepared directory.
4. **Restart and verify.** Run `docker compose up -d --force-recreate vauxr piper whisper`, then check saved devices, channels, and a voice interaction. If anything fails, stop the services and restore the backup or previous volume mounts.

## Connecting to other backends

Vauxr is backend-agnostic. If you're not using OpenClaw, connect your own LLM or agent service to the Vauxr WS protocol — see [ARCHITECTURE.md](./ARCHITECTURE.md) for the protocol spec.

## HTTP API

All endpoints require `Authorization: Bearer <channel-token>` — a `vx_ch_…` token issued by `POST /api/channels`. This is what the OpenClaw plugin uses.

**Devices**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/devices` | List connected devices and state |
| `PATCH` | `/api/devices/{id}` | Update device config (`name`, `voice`, `follow_up_mode`, `barge_in`, `button_actions`) |
| `POST` | `/api/devices/{id}/announce` | Push TTS announcement to a device |
| `POST` | `/api/devices/{id}/command` | Send control command (`set_volume`, `mute`, `unmute`, `reboot`, `ota`, `set_barge_in`) |
| `GET` | `/firmware/{name}.bin` | Serve an app image from `DATA_DIR/firmware/` for device HTTP OTA |

**Channels** — routing-channel CRUD. One channel is active at a time and receives the device's transcript.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/channels` | List channels (including the virtual `openclaw-direct` when `OPENCLAW_URL` is set) |
| `POST` | `/api/channels` | Create a channel; returns a `vx_ch_…` token (shown once) |
| `POST` | `/api/channels/{id}/activate` | Make this channel the active routing target |
| `POST` | `/api/channels/{id}/rotate` | Issue a new token for the channel; the old one stops working immediately |
| `DELETE` | `/api/channels/{id}` | Remove a channel (built-in channels can't be deleted) |

**Webhooks** — named HTTP endpoints configured in Settings, then selected per device gesture.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/webhooks` | List webhooks (authorization secrets are never returned) |
| `POST` | `/api/webhooks` | Create a webhook (`name`, `url`, optional `authorization`, optional JSON `body`) |
| `PATCH` | `/api/webhooks/{id}` | Update name / url / authorization / body |
| `DELETE` | `/api/webhooks/{id}` | Delete a webhook |
| `POST` | `/api/webhooks/{id}/duplicate` | Clone a webhook (copies url, authorization, and body; unique `{name} copy` / `{name} copy N`) |

A Postman collection is included at `postman/vauxr.postman_collection.json` covering devices, announce, control (including `ota` and `set_barge_in`), channels, webhooks, and firmware download. Run the Channels folder top-to-bottom to exercise create → activate → rotate → channel-token-auth → delete; run Webhooks the same way so Create captures `webhook_id`.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design and protocol spec, and [ROADMAP.md](./ROADMAP.md) for what's planned.

## Related

- [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) — OpenClaw channel plugin: exposes the HTTP API as agent tools so your OpenClaw agent can announce and control devices automatically

## License

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-8B5CF6?style=flat-square)](https://www.gnu.org/licenses/agpl-3.0)

Copyright © 2026 Lillian Mikus

Vauxr is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of this software as a network service, you must make your source available under the same license.
