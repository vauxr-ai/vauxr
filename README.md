# Vauxr

![Open Protocol](https://img.shields.io/badge/protocol-Vauxr_1.0-8B5CF6?style=flat-square)
![Docker Build](https://img.shields.io/github/actions/workflow/status/vauxr-ai/vauxr/publish.yml?branch=main&style=flat-square&label=docker%20build&color=8B5CF6)
![Docker Pulls](https://img.shields.io/docker/pulls/vauxr/vauxr?style=flat-square&logo=docker&color=8B5CF6)
![Latest Release](https://img.shields.io/github/v/release/vauxr-ai/vauxr?style=flat-square&include_prereleases&color=8B5CF6)
![Last Commit](https://img.shields.io/github/last-commit/vauxr-ai/vauxr/develop?style=flat-square&color=8B5CF6)

**An open-source, self-hosted voice assistant platform.** Connect microphones and
speakers to your agent, with local Whisper speech recognition and Piper speech
synthesis included in the Docker stack. OpenClaw is supported through the
[vauxr-openclaw plugin](https://github.com/vauxr-ai/vauxr-openclaw).

`Microphone → Vauxr → speech recognition → agent → speech synthesis → speaker`

## Documentation

| I want to… | Start here |
| --- | --- |
| Learn the system | [First control-plane success](docs/tutorial.md) |
| Complete a task | [Setup, integration and recovery guides](docs/how-to.md) |
| Look up a contract | [HTTP API](docs/http-api.md), [interface reference](docs/reference.md), [configuration](docs/configuration.md) |
| Understand design decisions | [Architecture](ARCHITECTURE.md) |

Browse the [documentation library](docs/README.md) for all paths.

## Quick Start

**Before starting:** install Git and Docker with Compose. Run these commands from
the repository root, with a browser on the Docker host. The default stack uses
host networking and ports 8080, 8765, 10200 and 10300; model downloads need network
access and disk space. See the [tutorial prerequisites](docs/tutorial.md#before-you-begin).

1. Clone the repo:

```bash
git clone https://github.com/vauxr-ai/vauxr.git
cd vauxr
```

2. Build and start the stack:

```bash
docker compose up -d --build
```

Compose automatically prepares the bind-mounted storage before starting the
services. Vauxr still runs as non-root UID 100/GID 101. Initialization adjusts
only the data, recordings, and firmware directory roots, never existing files
or speech caches. It is safe to repeat and works through the selected Docker
daemon with rootful or rootless Docker; no host `chown` is needed. If storage
initialization fails, startup stops: inspect `docker compose logs storage-init`,
fix the mount permissions, then repeat the command above. See
[storage initialization and recovery](docs/how-to.md#automatic-storage-initialization).


3. On the same machine, open the web client at [http://localhost:8080](http://localhost:8080).
Then open the local owner claim flow from a private terminal:

```bash
docker compose exec -it vauxr vauxr-owner claim
```

Enter the one-time code in the browser, save the generated operator token in a
password manager, acknowledge that save, and then log in. No auth token or
`.env` file is required to start Vauxr. Legacy `DEVICE_TOKEN` grants no access.

For a browser on another machine, configure its [exact owner origin](docs/how-to.md#browser-on-another-machine-or-address)
first. See [HTTPS/proxy setup](docs/how-to.md#optional-https-and-proxy),
[OpenClaw enrollment](docs/how-to.md#connecting-to-openclaw), and
[persistent data and migration](docs/how-to.md#persistent-data) for those tasks.
Shared device/channel tokens grant no access; legacy configuration is not
migrated automatically. Verify compatible server, plugin and firmware revisions
before upgrading.

Other backends can implement the [agent protocol](ARCHITECTURE.md).
Planned work is tracked in the [roadmap](ROADMAP.md).

## License

Copyright © 2026 Lillian Mikus. Licensed under [AGPL-3.0](LICENSE).
If you run a modified version as a network service, make its source available
under the same license.
