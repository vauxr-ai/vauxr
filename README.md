# Vauxr

![Open Protocol](https://img.shields.io/badge/protocol-Vauxr_1.0-8B5CF6?style=flat-square)
![Docker Build](https://img.shields.io/github/actions/workflow/status/vauxr-ai/vauxr/publish.yml?branch=main&style=flat-square&label=docker%20build&color=8B5CF6)
![Docker Pulls](https://img.shields.io/docker/pulls/vauxr/vauxr?style=flat-square&logo=docker&color=8B5CF6)
![Latest Release](https://img.shields.io/github/v/release/vauxr-ai/vauxr?style=flat-square&include_prereleases&color=8B5CF6)
![Last Commit](https://img.shields.io/github/last-commit/vauxr-ai/vauxr/develop?style=flat-square&color=8B5CF6)

**An open-source, self-hosted voice assistant platform.** Connect microphones and
speakers to your agent. Connect your own Wyoming speech recognition and speech
synthesis services for Standard voice. OpenClaw is supported through the
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

**Before starting:** install Python 3.12, Node.js 22 and Git. Run commands from
the repository root. Ports 8080 and 8765 must be free. The native path needs no
Docker or `.env` file. It starts the gateway and owner portal; a voice turn also
needs your own STT/TTS services and an agent. See the [tutorial](docs/tutorial.md).

1. Clone the repo:

```bash
git clone https://github.com/vauxr-ai/vauxr.git
cd vauxr
```

2. Install the gateway with WebRTC support and build the owner portal:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[realtime]'
.venv/bin/python -m nltk.downloader punkt punkt_tab
npm --prefix web-client ci
npm --prefix web-client run build
```

3. Start Vauxr from the repository root:

```bash
.venv/bin/vauxr
```

4. Open [http://localhost:8080](http://localhost:8080). In another terminal,
   run the owner claim command from the repository root:

```bash
.venv/bin/vauxr-owner claim
```

Enter the one-time code in the browser, save the generated operator token in a
password manager, acknowledge that save, and then log in. No auth token or
`.env` file is required to start Vauxr. Legacy `DEVICE_TOKEN` grants no access.

Vauxr enables WebRTC by default for capable clients. With no configuration, the
trusted origin is `http://localhost:8080`; its local IPv4 address is selected
for ICE. For a physical device or remote browser, set the exact reachable
`OWNER_HTTP_ORIGIN` (or HTTPS origin) first. If it resolves to a local IPv4
interface, Vauxr uses that address for ICE. Otherwise set `REALTIME_HOST`
explicitly; a client falls back to WebSocket when no usable ICE host is found.

### Speech and agents

No speech model is downloaded or started by the native install. Standard voice
uses Wyoming endpoints, initially `tcp://127.0.0.1:10300` for STT and
`tcp://127.0.0.1:10200` for TTS. Start compatible services yourself or configure
their actual endpoints and voices in the owner portal. A listed provider does
not mean that service is running. Then connect an agent such as OpenClaw; see
[speech configuration](docs/configuration.md#speech-providers-and-selections) and
[agent setup](docs/how-to.md#connecting-to-openclaw). This is a control-plane
quick start until those external services are connected.

### Optional Docker deployment

Docker Compose starts only the gateway. It uses bridge networking and disables
WebRTC because aiortc uses dynamic ICE UDP ports in the current container path.
Docker WebRTC without host networking is tracked in [spike #128](https://github.com/vauxr-ai/vauxr/issues/128).
Its default image omits Pipecat/aiortc; advanced host-network deployments can
build with `--build-arg VAUXR_EXTRAS=realtime,acme` when configured separately.
If persisted `data/config.json` already enables realtime, set its `realtime.enabled`
to `false` before starting the bridge deployment; saved settings take precedence
over Compose defaults.
For another machine to connect to the container, also set
`VAUXR_BIND_ADDRESS=0.0.0.0` and the exact owner origin.

```bash
docker compose up -d --build
docker compose exec -it vauxr vauxr-owner claim
```

To **also** start the optional Whisper/Piper containers, run
`docker compose --profile local-speech up -d --build`. These images and their
models are large and may download for several minutes. The gateway does not
wait for their health checks. On a fresh install with this profile, the gateway
uses their Compose DNS names; a saved `data/speech.json` retains its existing
endpoints and must be updated when moving between native and container runs.
Piper's warnings about optional Japanese and Thai voices do not affect its
default English voice. Compose prepares bind-mounted storage automatically;
see [storage recovery](docs/how-to.md#automatic-storage-initialization).

When moving an existing installation from Docker to native Python, stop the
container before starting the native process against the same `./data` directory.
Persisted `data/config.json` settings win over new defaults: change
`realtime.enabled` to `true` if it was previously disabled. Update saved
`data/speech.json` provider hosts from Compose names (`whisper`/`piper`) to
endpoints reachable from the Mac, or configure new services in the portal.

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
