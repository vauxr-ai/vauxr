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

**Breaking auth upgrade:** follow the [clean install and migration guide](docs/authz/migration-52.md)
for exact compatible source pins, owner setup, private backup/rollback, device re-pairing
and OpenClaw reconnect. Shared device/channel tokens grant no access. The pinned
auth stack remains under review; firmware build and physical acceptance are still required.

```
Device (mic) → vauxr → Whisper (STT) → LLM backend → Piper (TTS) → Device (speaker)
```

Any device that speaks the Vauxr WS protocol can connect. The HTTP API (`/api/devices`) lets your backend push announcements to devices and send control commands without a voice turn.

## Quick Start

1. Clone the repo:

```bash
git clone https://github.com/vauxr-ai/vauxr.git
cd vauxr
```

2. Prepare the persistent directory and start the stack:

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

3. On the same machine, open the web client at [http://localhost:8080](http://localhost:8080).
Then open the local owner claim flow from a private terminal:

```bash
docker compose exec -it vauxr vauxr-owner claim
```

Enter the one-time code in the browser, save the generated operator token in a
password manager, acknowledge that save, and then log in. No auth token or
`.env` file is required to start Vauxr. Legacy `DEVICE_TOKEN` grants no access.

### Browser on another machine or address

The default owner origin is exactly `http://localhost:8080`, so it is intended
for a browser on the Docker host. If the browser instead uses a LAN IP,
hostname, or a different external port, set `OWNER_HTTP_ORIGIN` to that exact
browser origin before starting Vauxr. A `.env` file is one optional mechanism;
an exported environment variable or your deployment configuration works too.

```bash
# Example only: use the exact URL entered in the browser.
OWNER_HTTP_ORIGIN=http://192.168.1.20:8080 docker compose up -d
```

Restart the stack and run the claim command with the same deployment
environment after changing the origin. The server never learns a trusted origin
from a browser request, Host header, or DNS lookup. See
[owner authentication v1](docs/authz/owner-v1.md) for the exact-origin boundary.

### Optional native HTTPS

[Native HTTPS/WSS](docs/native-https.md) runs in the Vauxr container, using your
own certificates or explicitly enabled Let's Encrypt Route53 automation. Set
`HTTPS_ENABLED=1` and an exact `OWNER_HTTPS_ORIGIN`; follow the guide for certificate,
port and renewal settings. No proxy sidecar is required. Invalid TLS configuration
fails closed. The existing HTTP and device WS listeners remain separate compatibility
endpoints, and owner access requires HTTPS in this mode.

Existing deployments that terminate TLS at a proxy can continue using
`OWNER_HTTPS_ORIGIN` and `OWNER_TRUSTED_PROXIES` with native HTTPS disabled.

Voice devices connect to `ws://localhost:8765` from the Docker host, or to the
corresponding reachable server authority when they are remote.

## Connecting to OpenClaw

The recommended path is the [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) channel plugin, installed from [ClaWHub](https://clawhub.ai):

```bash
openclaw plugins install clawhub:@vauxr/openclaw
```

The plugin wires OpenClaw to your Vauxr server and exposes device announcements and controls as agent tools. See the [vauxr-openclaw README](https://github.com/vauxr-ai/vauxr-openclaw) for configuration.

Integration clients use [integration enrollment v1](docs/authz/integration-v1.md):
request access, have the owner approve the displayed code, then automatically
receive and durably save a one-time credential before acknowledging it. The
credential grants device listing, announcements, control, firmware update initiation,
physical-device pairing initiation/approval and its own channel voice responses.
It excludes owner and credential management. Rotation/revoke use the separate
[lifecycle v1](docs/authz/lifecycle-v1.md) owner controls and leave device credentials
independent. Client UI and real plugin persistence acceptance are separate work.

## Persistent data

Vauxr bind-mounts `./data` beside this Compose file into `/data`. This directory
holds the private authorization snapshot (`authz.json`, schema 5 after integration
enrollment), device settings (`devices.json`), webhooks (`webhooks.json`), channels
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

Authorization is per operation; see the [route inventory](docs/authz/inventory.md).
Integrations send `Authorization: Bearer <vx_int_credential>` after enrollment/save
ACK. Owner operations require the owner session cookie and, for mutations, exact
Origin plus CSRF. Device credentials grant only their device transport and firmware
read access. Enrollment and lifecycle use their versioned contracts below.

**Devices**

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/devices` | List connected devices and state |
| `PATCH` | `/api/devices/{id}` | Update device config (`name`, `voice`, `follow_up_mode`, `barge_in`, `button_actions`) |
| `POST` | `/api/devices/{id}/announce` | Push TTS announcement to a device |
| `POST` | `/api/devices/{id}/command` | Send control command (`set_volume`, `mute`, `unmute`, `reboot`, `ota`, `set_barge_in`) |
| `GET` | `/firmware/{name}.bin` | Authenticated firmware read (owner or device); unchanged |
| `POST` | `/api/firmware-delivery/{filename}` | Owner session + Origin + CSRF: mint a legacy OTA delivery URL for an existing `.bin` |
| `GET` | `/firmware-delivery/{token}/{filename}` | Single-use capability download; no Authorization header required |

Mint returns `201` with `{"url": "<configured owner origin>/firmware-delivery/<token>/<filename>",
"expires_in": 120}`. The configured owner origin must be reachable by the device.
The URL serves the binary directly, without redirects or query/header authentication.
Delivery tokens are random (256 bits), stored only as hashes in process memory, bound
to the exact filename, and expire using a monotonic clock. At most 128 can be live;
minting returns `503` when full, and expired entries are reclaimed on mint.
Restarting the process invalidates all outstanding URLs. Route mint and download to
the same server process if using multiple workers.

A redemption attempt consumes the token before opening/streaming the file, including
filename mismatches and failed downloads. Retry requires a newly minted URL; HEAD
cannot redeem it. Invalid, expired, replayed, mismatched and missing artifacts return
the same `404`. Non-regular files, symlinks and traversal are rejected. Responses are
`no-store`. Treat the URL as a temporary bearer secret: do not log or persist it.
The server disables aiohttp access logs; reverse proxies must also disable or redact
request paths for `/firmware-delivery/` and must not cache these responses.

**Channels** — owner-controlled routing metadata. One channel is active at a time and receives the device's transcript.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/channels` | List channels (including the virtual `openclaw-direct` when `OPENCLAW_URL` is set) |
| `POST` | `/api/channels` | Legacy credential creation unavailable (owner 501); use integration enrollment v1 |
| `POST` | `/api/channels/{id}/activate` | Make this channel the active routing target |
| `POST` | `/api/channels/{id}/rotate` | Legacy rotation unavailable (owner 501); use lifecycle v1 |
| `DELETE` | `/api/channels/{id}` | Legacy credential deletion unavailable (owner 501); use lifecycle v1 revoke |

**Webhooks** — named HTTP endpoints configured in Settings, then selected per device gesture.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/webhooks` | List webhooks (authorization secrets are never returned) |
| `POST` | `/api/webhooks` | Create a webhook (`name`, `url`, optional `authorization`, optional JSON `body`) |
| `PATCH` | `/api/webhooks/{id}` | Update name / url / authorization / body |
| `DELETE` | `/api/webhooks/{id}` | Delete a webhook |
| `POST` | `/api/webhooks/{id}/duplicate` | Clone a webhook (copies url, authorization, and body; unique `{name} copy` / `{name} copy N`) |

A Postman collection is included at `postman/vauxr.postman_collection.json` covering devices, announce, control (including `ota` and `set_barge_in`), channels, webhooks, and firmware download. Its legacy channel-token workflow is superseded by integration enrollment v1 and lifecycle v1; it is not the current authentication contract.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full system design and protocol spec, and [ROADMAP.md](./ROADMAP.md) for what's planned.

## Related

- [vauxr-openclaw](https://github.com/vauxr-ai/vauxr-openclaw) — OpenClaw channel plugin: exposes the HTTP API as agent tools so your OpenClaw agent can announce and control devices automatically


## License

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-8B5CF6?style=flat-square)](https://www.gnu.org/licenses/agpl-3.0)

Copyright © 2026 Lillian Mikus

Vauxr is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). If you run a modified version of this software as a network service, you must make your source available under the same license.
