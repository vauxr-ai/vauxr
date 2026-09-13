# Speech provider selection

Speech settings are management configuration. Devices send audio/control messages,
never endpoint URLs or provider/voice selections. The existing device `voice`
field remains a **boolean** voice-formatting/enable flag; `voice_id` is a separate
speech identity. No firmware changes are required.

`src/speech.py` owns the registry, persistence and immutable complete selection.
The shared Wyoming adapters (`wyoming_stt.py`, `wyoming_tts.py`) own transport and
wire voice mapping; orchestration never branches on Whisper/Parakeet/Piper/Kokoro.
Each backend ID represents **one model deployment**, not a request to load a model.
Whisper and Parakeet v3 use Wyoming audio/transcript events. Piper and Kokoro use
Wyoming synthesize with `voice.name` equal to the configured voice ID. Use a
Wyoming-compatible service configured to serve that model and those wire names.

## Operator configuration

The built-in IDs `whisper` and `piper` preserve `WHISPER_URL`, `PIPER_URL`, and
`PIPER_VOICE` behavior and defaults. Without new files, selection is unchanged.
Additional endpoints are declared in `DATA_DIR/speech-providers.json` (restart to
reload). The file is operator-owned, never writable through HTTP. Example:

```json
[
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

Current legacy management bearer authorization applies to **both reads and writes**:

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

`http_server._authorize_speech_management` is the explicit compatibility seam
injected into `speech_http.attach_speech_routes`. On this base it delegates to the
existing shared `DEVICE_TOKEN` **or channel-token** management check. Legacy auth
cannot distinguish a shared-token device holder from management; this change
neither fixes nor broadens that existing trust model. WS/offer JSON cannot alter
speech settings. Announcement/button permissions remain at their existing entry
points and do not grant speech configuration permissions.

Read-only inspection of the #45/#49 workstreams (`vauxr-auth-scopes`,
`vauxr-auth-owner`, `vauxr-auth-enrollment`) found deny-by-default `Operation`
policy, owner sessions and scoped device/integration roles. No auth files or
branches were copied, changed, merged or rebased. Integration should:

- Map global speech read/write and registry readiness to owner `server.manage`
  (or explicitly agreed narrower speech operations). That operation is currently
  marked unshipped in the scopes branch; do not blindly inherit its 501 guard.
- Map device speech read/write to owner `device.configure`, passing the device ID
  as resource. `devices.list` alone must not grant configuration/readiness access.
- Replace the injected legacy authorization callback with owner/session policy;
  preserve its 401/403 distinction, transport boundary and session CSRF checks.
  The callback may raise the appropriate aiohttp HTTP exception for denial.
- Extend the upcoming route/operation allowlist explicitly: these routes use a
  shared handler and must be classified by path and method. Deny device and
  integration credentials access to speech settings. Do not keep the legacy
  channel-token fallback after scoped auth lands.
- Adapt the web component's fetch transport to the owner session/CSRF client at
  integration time; this branch intentionally retains current bearer UI behavior.

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
shown as unavailable. Connection/read timeouts also bound the shared clients.

`realtime_wyoming` still buffers **the entire TTS segment** before passing PCM to
Pipecat. WS synthesis streams received PCM. This feature does not redesign
streaming, validate GPU operation, benchmark latency, or verify Whisper,
Parakeet v3, Piper or Kokoro models on hardware. Tests use fake local Wyoming
peers and mocked inference, including real Pipecat adapter contracts where the
optional dependency is installed. No live voice service or model is required.
