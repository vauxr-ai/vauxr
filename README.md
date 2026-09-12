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

Use `scripts/migrate-data.py` to inspect the current layout and, during maintenance,
copy selected old data into the repository's binds. It supports legacy named volumes,
Vauxr already bound with named caches, and **already recreated containers** bound to
`./data`, `./data/piper`, and `./data/whisper`. The original containers need not exist;
use the current three containers for mount and consumer checks. An `already-bound`
status describes mounts only: it does **not** establish that old data was migrated.
Identity-file and model-cache timestamps cannot prove data provenance or migration
completeness either.

Run on the Docker host, from this repository, with the daemon/context that owns
the current three containers and the old source volumes:

```bash
python3 scripts/migrate-data.py                 # read-only discovery, even while running
# Arrange maintenance; disable restart automation and host writers. Apply stops running consumers.
# With original named mounts still present, discovery can use their exact volumes.
python3 scripts/migrate-data.py --apply
# Plain status output for terminals or automation (also works with --apply):
python3 scripts/migrate-data.py --plain
```

If containers were already recreated, first inspect the surviving volumes and the
destination without stopping anything:

```bash
cd /path/to/vauxr
python3 scripts/migrate-data.py --inspect-candidates --plain
python3 scripts/migrate-data.py --plain
```

Candidate inspection uses only Docker volume listing/inspection and host filesystem
metadata reads; it creates no helper container or files. It lists both
`vauxr_{vauxr,piper,whisper}-data` and `vauxr-local_{vauxr,piper,whisper}-data` if present,
plus other prefixes with these service suffixes. It **never chooses between sets**,
even if only one set exists. `--inspect-volume EXACT_NAME` adds another candidate
(repeatable). Inspection also works when no service containers remain; apply still
requires the current three containers so nested mounts can be validated.

The report includes volume creation times and at most 200 entries per tree with
filenames, types, sizes, and UTC modification times. It never opens file contents,
prints symlink targets, or dumps container environments/volume labels. Keep filenames
private as needed. Truncated, inaccessible, or changing listings are explicitly marked.
Host listings are advisory: path visibility is not verified until apply, and rootless
namespaces or host permissions may prevent listings. No automatic elevation or daemon
switch occurs. An unavailable listing or matching metadata is not proof of empty or
identical data; establish the intended source before applying.

After reviewing the evidence, set each exact volume name yourself. These placeholders
are deliberately not a recommendation for either surviving prefix:

```bash
VAUXR_SOURCE='REPLACE_WITH_SELECTED_VAUXR_VOLUME'
PIPER_SOURCE='REPLACE_WITH_SELECTED_PIPER_VOLUME'
WHISPER_SOURCE='REPLACE_WITH_SELECTED_WHISPER_VOLUME'
python3 scripts/migrate-data.py --plain \
  --source-vauxr "$VAUXR_SOURCE" --source-piper "$PIPER_SOURCE" \
  --source-whisper "$WHISPER_SOURCE" --backup-existing
# Review planned stops and arrange maintenance.
# Disable filesystem writers and restart automation; apply stops EVERY running consumer:
python3 scripts/migrate-data.py --plain \
  --source-vauxr "$VAUXR_SOURCE" --source-piper "$PIPER_SOURCE" \
  --source-whisper "$WHISPER_SOURCE" --backup-existing --apply
```

Each `--source-*` overrides that service's current mount with an existing, exact
named volume. Bound caches require explicit `--source-piper` and `--source-whisper`
for apply; discovery never assumes the newly populated caches are the old source.
Omitting `--source-vauxr` intentionally preserves the current Vauxr bind as the
source; specify it to recover an old Vauxr identity/settings volume as well.
`--backup-existing` permits recovery into a populated target by retaining the **whole
existing `./data` directory**, including current identity, both caches, and hidden
underlying recordings/firmware entries, at `.data-backup-<id>`. The new directory
contains the selected sources, with the exclusions below; this is a replacement
with a full backup, never an implicit merge. Current-only files remain in the backup.
The separate `./firmware` and `./recordings` host directories are not changed.
Inspection flags cannot be combined with `--apply`.

Status messages go to stderr, with colors and emojis only when both stdout and
stderr are TTYs and the terminal encoding supports the icons. `--plain` disables
both; setting `NO_COLOR` (even to an empty value), `TERM=dumb`, or redirecting either
stream also selects plain output. The JSON plan, recovery details and error messages
remain undecorated, and exit codes are unchanged. Stdout retains the JSON plan
followed by dry-run or helper/recovery text; it is not a standalone JSON document.
The `VERIFIED` status confirms the repeated mount and stopped-consumer check,
not file-content or service verification; follow the post-publication checks below.

Use `--vauxr NAME --piper NAME --whisper NAME` for different container names and
`--root /absolute/path/to/vauxr` for another repository location. Dry-run lists
sources and consumers by name and full ID, clearly identifying planned stops; it never
stops containers, runs helpers, or writes files. On apply, selection, layout, flags,
helper image and read-only daemon-side destination/source validations complete first.
Then every discovered running consumer (including outside this stack and overlapping
bind mounts) is automatically stopped using `docker stop --time=-1`: graceful shutdown
with no forced kill or timeout escalation. An unresponsive consumer can therefore
wait indefinitely. Exited/created consumers need no stop. Paused, restarting,
removing, dead or unknown states fail safely before any stop; resolve them manually.
Stop failures abort before copying, and containers already stopped stay stopped.

The script verifies each stopped container, rediscovers all consumers before copying,
and checks again immediately before publication while the helper holds the filesystem
lock, and after publication. A restart or changed inventory aborts; a race detected
after publication requires inspection of the published data and recovery paths.
Docker has no atomic storage-consumer lock: keep restart automation and all other
filesystem writers disabled throughout maintenance. The final check-to-rename window
cannot be eliminated. The helper retains private staging on failure and times out
without publication if the client never authorizes publication. The script never
starts or recreates services, even after success: directory inode swaps require
manual recreation of all affected consumers with the intended mounts.

Requirements and limitations:

- Linux, Python 3, Docker CLI, a local Unix-socket daemon, and an already installed,
  trusted `python:3.12-slim` helper image (Python, GNU coreutils, `renameat2` support).
  `--helper-image` selects an alternative compatible image. Apply pins its inspected
  image ID and runs it with no network, no pull, and a read-only container root.
- Plain `local` named volumes without driver options only; no remote Docker,
  Docker Desktop path translation, external volume drivers, cross-daemon or
  rootful-to-rootless migration, per-container user-namespace overrides, or volume
  subpaths. A private probe verifies that the daemon sees the
  same repository path. In a containerized shell, socket access alone is insufficient:
  the repository must also exist at the identical path in both namespaces.
- The repository path must be canonical, without symlinks or commas. `data` must
  be a real directory, not a symlink or mount point. Allow disk space for a complete
  staged copy plus the retained original. Regular files, directories and symlinks
  are supported; special files fail closed. No concurrent host writers are supported.
- Without `--backup-existing`, for legacy named Vauxr data, `./data` must be absent
  or empty. For existing bound Vauxr data, `data/piper` and `data/whisper` must be
  absent or empty real directories.
  Populated targets are refused unless the explicit backup flag is supplied;
  it does not bypass the lock, mount, ownership, or stopped-consumer checks. Legacy
  `/data` containing `piper` or `whisper` entries is refused as ambiguous.
- Only separate bind mounts at `/data/recordings` and `/data/firmware` are supported
  below Vauxr `/data`; their host paths must be outside `./data`. Those entries are
  excluded from the copy, including any hidden underlying contents. Other nested
  mounts are refused. Keep the existing recordings/firmware Compose mounts intact.
- Copying and ownership preservation run as UID 0 **inside the selected daemon's
  namespace**, including rootless Docker. Files retain numeric ownership, modes,
  timestamps, links and supported extended attributes/ACLs; metadata errors abort.
  No blanket host `chown` is performed. The host filesystem must support the source
  metadata. Cross-file hard links between separately copied top-level entries,
  inode numbers, ctime, and filesystem-specific flags are not preserved.

Apply copies into private `.data-migration-<id>/payload`, then publishes with
no-replace renames. An existing `data` directory is retained as
`.data-backup-<id>`; backups from earlier runs are never overwritten or deleted.
Source volumes are mounted read-only and remain untouched. Treat staged copies and
backups as sensitive data; all migration paths are ignored by Git.

After successful publication, manually recreate the three services using the new
Compose binds during the same maintenance window. Even already-bound containers may
still reference the original directory inodes after publication, so all three must
be recreated before resuming service. Verify
saved settings, channel authentication, cache contents, ownership, model-service
health and a voice turn before considering the migration complete.

If migration fails after stops, consumers remain stopped; the error lists their names/IDs
and precise recovery paths. Stop failures can leave some consumers running: resolve
those states before recovery. If copying fails, `data` remains unchanged and staging
is retained. If publication fails after moving the original, the script attempts to restore it without replacing
anything. An interruption or failed recovery can leave `data` missing: inspect the
printed recovery paths with services still stopped. The complete original is in
`.data-backup-<id>` if it was moved; otherwise it remains at `data`. Preserve any
partially published `data` under a new secure name before restoring the backup.
Do not blindly rerun or publish incomplete staging. Resolve the failure and retry
from the intact source volumes/original bind. The persistent lock file is harmless;
the kernel releases its lock when the helper exits. If the client was interrupted,
verify the migration helper has exited before recovery.

For rollback after service verification fails, manually stop all consumers and retain
the new `data` under a separate backup name before restoring `.data-backup-<id>` to
`data`. For the already-bound layout, keep the current Compose binds and recreate
all three services against the restored directory. For a legacy named-volume layout,
restore the previous Compose mounts using the **exact names in `current_layout`**
and recreate with those mounts; explicit recovery sources may differ from those
previous mounts. Never run `docker compose down -v`, prune volumes, or delete source
volumes as part of this procedure.

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
