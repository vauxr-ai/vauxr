# Speech provider selection

Speech settings are management configuration. Devices send audio/control messages,
never endpoint URLs or provider/voice selections. The existing device `voice`
field remains a **boolean** voice-formatting/enable flag; `voice_id` is a separate
speech identity. No firmware changes are required.

`src/speech.py` owns persistence, inheritance and shared selection resolution;
`speech_models.py` defines provider-neutral deployments and immutable complete
selections. `speech_catalog.py` owns the bounded server-side adapter catalog,
legacy deployment defaults and wire voice mapping. `wyoming_protocol.py` owns
shared events/framing/errors; `wyoming_stt.py` and `wyoming_tts.py` are generic
transport clients. Orchestration never branches on Whisper/Parakeet/Piper/Kokoro.
Each backend ID represents **one model deployment**, not a request to load a model.
Whisper and Parakeet v3 use Wyoming audio/transcript events. Piper and Kokoro use
Wyoming synthesize with `voice.name` equal to the configured voice ID. Use a
Wyoming-compatible service configured to serve that model and those wire names.

## Operator configuration

The built-in IDs `whisper` and `piper` preserve `WHISPER_URL`, `PIPER_URL`, and
`PIPER_VOICE` behavior and defaults. Without new files, selection is unchanged.
The internal config fields are `stt` / `tts` (`WyomingEndpoint` /
`WyomingTTSConfig`). Operators may migrate environment names on restart:

| Preferred name | Legacy fallback | Unchanged default |
| --- | --- | --- |
| `STT_URL` | `WHISPER_URL` | `tcp://whisper:10300` |
| `TTS_URL` | `PIPER_URL` | `tcp://piper:10200` |
| `TTS_VOICE` | `PIPER_VOICE` | `en_US-libritts_r-medium` |

A nonempty preferred value wins; an empty or unset value uses the legacy value,
then the default. These are server environment settings only. Migrating env
names preserves the built-in IDs and existing `speech-settings.json` selections;
no persisted file rewrite is required. Those IDs remain legacy catalog slots,
not inference-time model selectors. To describe a different deployment accurately,
add a catalog entry with its own opaque ID/model label and select it through the
existing settings API/UI. Changing or removing an explicitly selected voice
continues to fail resolution until settings are updated.

Compose passes both neutral and legacy names to the gateway, with the same
precedence. Its unchanged host-network URL defaults are `tcp://127.0.0.1:10300`
and `tcp://127.0.0.1:10200`. The bundled Whisper/Piper services and health
dependencies remain; selecting an external endpoint does not remove those
services or change their provisioned models/voices.

Additional endpoints are declared in `DATA_DIR/speech-providers.json` (restart to
reload). The file is operator-owned, never writable through HTTP. Example:

```json
[
  {
    "id": "recognizer-east",
    "kind": "stt",
    "adapter": "wyoming",
    "model": "operator-asr-2026",
    "host": "speech-east",
    "port": 10400
  },
  {
    "id": "speaker-east",
    "kind": "tts",
    "adapter": "wyoming",
    "model": "operator-voice-2026",
    "host": "speech-east",
    "port": 10401,
    "voices": ["narrator-a", "narrator-b"]
  },
  {
    "id": "parakeet-v3-local",
    "kind": "stt",
    "adapter": "parakeet-v3",
    "model": "parakeet-tdt-0.6b-v3",
    "host": "parakeet",
    "port": 10301
  },
  {
    "id": "kokoro-local",
    "kind": "tts",
    "adapter": "kokoro",
    "model": "kokoro-82m",
    "host": "kokoro",
    "port": 10201,
    "voices": ["af_heart", "bf_emma"]
  },
  {
    "id": "piper-alternate",
    "kind": "tts",
    "adapter": "piper",
    "model": "en_US-lessac-medium",
    "host": "piper-alternate",
    "port": 10202,
    "voices": ["en_US-lessac-medium"]
  }
]
```

The catalog also accepts `"adapter": "wyoming"` for either kind when the
operator's service implements the same audio/transcript or synthesize/voice.name
contract. This does not add another protocol or dynamically load adapter code.
The resolver initializes an unpersisted store from its first STT and first TTS
entries; the catalog supplies legacy entries first to preserve default behavior.
Neither IDs nor model labels are interpreted by routing code.

These are illustrative deployment names, not provisioned services. At most 32
backends total (including the two built-ins); IDs must be unique. Multiple model
endpoints may use the same adapter. Additional TTS models initially default to
the first declared voice. Registry edits do not install or download anything.
Pin each service to its intended model outside Vauxr; the model label is not sent
as a runtime model-switch command.

Settings → Global speech defaults selects STT/TTS and the selected model's voice.
Select another TTS backend to edit its own voice default; previous model voice
defaults remain stored. Expand a device for independent STT/TTS inheritance and
model-scoped voice overrides. Each dropdown shows its inheritance state and the
panel shows effective STT/TTS/voice. Refresh speech obtains current defaults and
availability. Reset to defaults removes every speech override for that device.
New devices inherit dynamically; global edits affect only fields still inherited.
A pinned TTS backend can still inherit that model's global voice default.

`DATA_DIR/speech-settings.json` persists global defaults and sparse device
speech overrides using atomic replacement. It is separate from `devices.json`
and `config.json`. Settings survive restarts. Back up all three. Removed backend
IDs/voices are retained as unresolved selections; restore the registry entry or
reset the affected override. There is no silent fallback to a different voice.

## API and authorization integration

Owner cookie sessions authorize **both reads and writes** through the explicit
`speech.configure` policy operation:

- `GET /api/speech`: registry projection, defaults and effective global selection.
- `PATCH /api/speech`: partial global `stt_backend`, `tts_backend`, `voices` map.
- `GET /api/devices/{device_id}/speech`: same projection plus sparse overrides and
  effective selection for that device, including offline/not-yet-connected IDs.
- `PATCH /api/devices/{device_id}/speech`: partial overrides; `null` resets a field.
  `voices: null` resets all model voices; `voices: {"backend-id": null}` resets one.

Example global body: `{"tts_backend":"kokoro-local","voices":{"kokoro-local":"af_heart"}}`.
Example device reset: `{"stt_backend":null,"tts_backend":null,"voices":null}`.
Unknown fields, endpoint URLs, wrong-kind IDs, or voices outside the named model
are rejected with 400 before any write. Endpoint host/port are excluded from the
HTTP projection. Announcement synthesis failures return HTTP 503 and emit a
device TTS error plus audio.end. An unavailable but configured backend can be selected for later
use; runtime failures produce errors, not automatic rerouting or downloads.

`http_server._authorize_speech_management` is the explicit authorization seam
injected into `speech_http.attach_speech_routes`. PR60 reconciles merged PR58 with
the scoped auth stack: the callback resolves a fresh owner session and checks the
owner-only `speech.configure` operation for both global and per-device reads and
writes. The shared handler declares its guarded boundary; all four routes are in
the route inventory. Unknown/legacy/operator bearers receive 401; authenticated
device and integration bearers receive 403. No channel-token fallback remains.

Owner middleware preserves exact configured Host/Origin, mode/proxy validation,
CSRF on PATCH, and no-store responses. SpeechSettings uses same-origin `ownerFetch`
with the HttpOnly session cookie and in-memory CSRF; it sends no bearer or inferred
HTTP port. HTTP administration works without browser voice or microphone access.
The operation is separate from the reserved, broader `server.manage` grant.

This is the required PR58/#46/#50 integration dependency, implemented and reported
in PR60. Speech URLs, request/response fields, validation, persistence, readiness,
provider selection and turn snapshots retain PR58's contracts. Devices/WS/offer
input still cannot select speech configuration. No auth middleware is relaxed.

## Turn consistency and readiness limits

WS snapshots at authenticated `voice.start`; direct text/button turns snapshot
before routing. STT, every queued TTS segment, and spoken backend-error messages
share that immutable object, including resolved endpoint addresses. Announcements
snapshot once per invocation. WebRTC cold pre-roll snapshots at `realtime.start`
and carries it through WS fallback or text seeding. Warm turns snapshot at the
accepted user speech start; the TTS adapter binds it at reply start and retains it
for every reply segment, keyed by Pipecat context ID so overlapping old/new
contexts cannot exchange selections. Interruption clears retained contexts. Global or device settings edits never mutate snapshots.

Readiness sends Wyoming `describe` and checks for the appropriate ASR/TTS
capability in `info`, with a two-second bound and bounded response buffer. It is
an on-demand protocol check, not proof that a model is loaded, a configured voice
exists on the remote service, or inference will succeed. Outages/timeouts are
shown as unavailable. Connection/read timeouts also bound the shared clients. Explicit Wyoming `error`
events fail immediately with a shared `WyomingError` (a `RuntimeError` subclass),
without exposing the remote error payload or waiting for the peer to disconnect.
Premature EOF uses the same error class.

`realtime_wyoming` still buffers **the entire TTS segment** before passing PCM to
Pipecat. WS synthesis streams received PCM. This feature does not redesign
streaming, validate GPU operation, or benchmark latency. Automated tests use fake local Wyoming
peers and mocked inference, including real Pipecat adapter contracts where the
optional dependency is installed. No live voice service or model is required.

## Optional local CPU services

The `speech-extra` Compose profile adds Parakeet v3 and Kokoro alongside the
existing Whisper/Piper services. Start just these services:

```sh
docker compose --profile speech-extra up -d parakeet-v3 kokoro
```

The images are pinned to the digests verified on 2026-09-13. Parakeet uses
[OHF-Voice/rhasspy Wyoming Whisper](https://github.com/OHF-Voice/wyoming-faster-whisper)
with explicit `--stt-library sherpa`,
`--model sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8`, `--device cpu`,
`--cpu-threads 4`, and `--language en`. Specifying v3 matters: automatic English
model selection may choose v2. Its dedicated `/data` cache receives the model
on first start. The pinned image contains `/usr/src/.venv/bin/python3` and
`wyoming_faster_whisper.health_check`; its Describe/ASR check targets internal
loopback port 10310 with a two-second timeout.

[Kokoro Wyoming](https://github.com/nordwestt/kokoro-wyoming) implements Wyoming
synthesis, not an OpenAI HTTP API. The pinned image's `--help` verifies `--uri`,
`--voice`, `--model`, and `--voices`. The wrapper seeds only missing model/voice
files from `/app/src` into its dedicated `/data` cache, then runs the upstream
entrypoint using the verified `/usr/bin/python3` (Python 3.12) with explicit
paths; `/usr/local/bin/python3` is absent in this pinned image.
`ONNX_PROVIDER=CPUExecutionProvider` selects CPU
in the packaged kokoro-onnx implementation. No GPU access is requested or tested.
Its healthcheck requires a Wyoming Info response with TTS capability.

Both use the ordinary Compose bridge and listen on `0.0.0.0` **inside their
containers**. Ports are published only on the Docker host's loopback interface:
`127.0.0.1:10310:10310` and `127.0.0.1:10210:10210`. Vauxr, Whisper and Piper retain
their existing host networking; Vauxr reaches the optional services at
`127.0.0.1:10310` / `127.0.0.1:10210` through those published ports. Compose service
DNS names are available to peers on that bridge, not to host-network Vauxr.
Do not configure Vauxr with `kokoro` / `parakeet-v3` DNS names or ephemeral bridge
IP addresses. A listener bound to container loopback would not accept published
port traffic. The healthchecks correctly use loopback *inside* each container.
See [Docker bridge networking](https://docs.docker.com/engine/network/drivers/bridge/).
Both retain restart policy `unless-stopped`. Cache defaults are `./data/parakeet-v3` and `./data/kokoro`;
`PARAKEET_CACHE_DIR` / `KOKORO_CACHE_DIR` can point to existing daemon-host paths.
Keep caches on durable storage. The initial model download can exceed the
five-minute healthcheck grace period on slow connections; inspect logs and wait
for readiness before registering. Starting this profile does not register or
select providers and adds no mandatory gateway dependency.

After checking Describe and actual local inference, merge these objects into the
**existing** mounted `DATA_DIR/speech-providers.json` array (create it if absent).
Preserve existing entries, ownership, modes, and all speech settings:

```json
[
  {
    "id": "recognizer-local-01",
    "kind": "stt",
    "adapter": "wyoming",
    "model": "parakeet-tdt-0.6b-v3-int8",
    "host": "127.0.0.1",
    "port": 10310
  },
  {
    "id": "speaker-local-01",
    "kind": "tts",
    "adapter": "wyoming",
    "model": "kokoro-82m-v1.0",
    "host": "127.0.0.1",
    "port": 10210,
    "voices": ["af_heart"]
  }
]
```

These IDs are opaque deployment keys; endpoints and wire voice metadata stay on
the server. `af_heart` was advertised among 54 voices by the pinned Kokoro image
and verified by synthesis. Restart only Vauxr to reload the registry. Confirm
all four providers are ready via authenticated `GET /api/speech`, and compare
current defaults and device overrides with the pre-change snapshot. Registration
does not change the selected Whisper/Piper defaults or write speech settings.

The local verification synthesized “The quick brown fox jumps over the lazy dog.”
with Kokoro through the project client, resampled it to mono 16-bit 16 kHz PCM,
and sent that fixed audio through the project Parakeet Wyoming client. The
transcript matched exactly. Audio stayed local; no speaker, device, or LLM turn
was involved. A subsequent review repeated Describe, both configured healthchecks,
and this inference smoke test using isolated containers on a user-defined bridge,
with a separate host-network client accessing loopback-published ports. The pinned
Python paths above were inspected directly in the images. This is a smoke test,
not a latency or recognition benchmark.

For service rollback, first restore the prior registry (or remove only the two
new objects) and restart Vauxr, then stop/remove only `parakeet-v3` and `kokoro`.
Keep cache directories for recovery. If selections were subsequently changed,
restore their prior values before removing the catalog entries. Do not run a
stack-wide `down` or remove Whisper/Piper/data volumes.
