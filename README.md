# Vauxr

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
