# Vauxr

![Open Protocol](https://img.shields.io/badge/protocol-Vauxr_1.0-8B5CF6?style=flat-square)
![Docker Build](https://img.shields.io/github/actions/workflow/status/vauxr-ai/vauxr/publish.yml?branch=main&style=flat-square&label=docker%20build&color=8B5CF6)
![Docker Pulls](https://img.shields.io/docker/pulls/vauxr/vauxr?style=flat-square&logo=docker&color=8B5CF6)
![Latest Release](https://img.shields.io/github/v/release/vauxr-ai/vauxr?style=flat-square&include_prereleases&color=8B5CF6)
![Last Commit](https://img.shields.io/github/last-commit/vauxr-ai/vauxr/develop?style=flat-square&color=8B5CF6)

**Vauxr is an open source self-hostable voice assistant platform** — it comes out-of-the-box with a fast, local voice pipeline with idle-pause detection and follow-up mode. Great for talking to your OpenClaw agent.

This repo comes pre-configured as a Docker stack that ships with [Wyoming](https://github.com/rhasspy/wyoming)-compatible Whisper (STT) and Piper (TTS) out of the box. Use it as-is, or as a blueprint for your own implementation.

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

Changing mounts does not copy old named-volume data. Use a maintenance window
on the Docker host and the same Docker context/daemon throughout. Keep original
volumes and backups; do not run `docker compose down -v` or prune volumes.
For existing installations, use this procedure instead of initializing data again
with the Quick Start commands.

1. **Identify the sources before changing anything.** List containers, including
   stopped ones, then inspect each relevant container by its exact ID:

   ```bash
   docker context show
   docker ps -a --format '{{.ID}} {{.Names}}'
   docker inspect --format '{{json .Config.Labels}}' CONTAINER_ID
   docker inspect --format '{{json .Mounts}}' CONTAINER_ID
   docker volume ls
   docker volume inspect EXACT_VOLUME_NAME
   ```

   Before recreation, record the `Type`, `Name`, `Source` and `Destination` of
   each service's `/data` mount, plus the separate `/data/firmware` and
   `/data/recordings` binds. The `/data` mount's `Name` identifies its actual
   named volume; a `bind` instead identifies an existing host directory.

   **If containers were already recreated**, their current binds do not identify
   the old volumes or prove that migration happened. Inspect surviving volumes
   and their Compose project/volume labels, old deployment configuration and
   backups. Both `vauxr_{vauxr,piper,whisper}-data` and
   `vauxr-local_{vauxr,piper,whisper}-data` may exist (or another project prefix).
   Select each exact Vauxr, Piper and Whisper source explicitly from deployment
   evidence and, if needed, read-only content inspection. Never choose by prefix,
   newest timestamp, or the mere presence of one candidate. If ownership of the
   data is unclear, leave it intact until you can establish the correct source.

2. **Stop all consumers before backing up or copying.** Prepare the copy image
   and private staging directories described below first. Inspect mounts of all
   containers from `docker ps -a`, including other Compose projects, for the
   selected volumes and overlapping source/destination host paths. Disable
   restart automation and host writers, stop every consumer by exact ID with
   `docker stop --time=-1 CONTAINER_ID`, and verify each is stopped with
   `docker inspect --format '{{.State.Status}}' CONTAINER_ID`. Keep them stopped
   through copying and verification; do not recreate the old containers yet.

3. **Back up populated destinations and copy into fresh staging.** Securely
   back up the entire existing `./data`, including any new identity/settings or
   caches created after recreation. Use a unique backup location outside the
   destination; never overwrite an earlier backup or merge into populated data.
   Keep the original source volumes/binds unchanged. Allow space for the backup
   and complete copies.

   An example manual archive copy for **one explicitly selected named volume**
   into a new, empty staging directory follows. Replace both placeholders; inspect
   the exact volume first, since a mistyped name can create an empty volume.
   Repeat separately for the selected Vauxr, Piper and Whisper volumes:

   ```bash
   docker run --rm --network none --user 0 \
     --mount type=volume,src=EXACT_VOLUME_NAME,dst=/source,readonly \
     --mount type=bind,src=/ABSOLUTE/EMPTY/STAGING_DIRECTORY,dst=/target \
     debian:bookworm-slim cp -a /source/. /target/
   ```

   Paths must be visible to the selected daemon. For an existing Vauxr bind,
   use its inspected absolute path as a read-only `type=bind` source instead.
   Perform backups and subsequent assembly with the same archive-preserving
   method: preserve numeric ownership, modes, timestamps, links and supported
   ACLs/extended attributes, and resolve any copy/metadata errors before continuing.
   Run copies as root **inside that daemon's namespace**, including for rootless
   Docker; do not apply a blanket host `chown` or switch daemons.

4. **Assemble and verify the replacement before publishing it.** In a fresh
   private directory, place the selected Vauxr data at the root and the selected
   model caches under `piper/` and `whisper/`. Exclude Vauxr's top-level
   `firmware/` and `recordings/` entries from this assembled copy: their original
   host directories and separate Compose binds must stay intact. Retain any
   hidden underlying entries in the original/backup. Resolve any existing
   Vauxr `piper/` or `whisper/` entries explicitly rather than silently merging
   them with the selected caches. Compare source and copied contents and metadata,
   including identity/settings and model files. Only after all copies succeed,
   retain the old `./data` under a unique secure backup name and put the verified
   assembled directory at `./data`, preserving metadata including the Vauxr
   source root directory ownership and mode.

5. **Recreate and check.** With the intended Compose project and file selected,
   run `docker compose up -d --force-recreate vauxr piper whisper` only after
   successful copying and publication. This also applies to already-bound
   containers, which may still reference the replaced directory. Recreate any
   other affected consumers with their intended mounts before resuming writers.
   Inspect the resulting mounts and check saved settings, channel authentication,
   ownership, both model services' health and a voice turn. On failure, stop all
   consumers again and preserve partial copies; restore the intact backup or
   the previous Compose mounts using the recorded exact volume names before
   recreating. Retain originals and backups until recovery is verified.

## Connecting to OpenClaw

The recommended path is the [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) channel plugin, installed from [ClaWHub](https://clawhub.ai):

```bash
openclaw plugins install clawhub:@vauxr/openclaw
```

The plugin wires OpenClaw to your Vauxr server and exposes device announcements and controls as agent tools. See the [vauxr-openclaw README](https://github.com/vauxr-ai/vauxr-openclaw) for configuration.

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
