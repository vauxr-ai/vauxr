# Transport API reference

This reference describes server source **25469df63362853a2a4daf18cc0a115c6626b6bf**.
For REST operations see [HTTP API](api.md); for identity and routing see
[types and instances](concepts.md). The [coverage ledger](https://github.com/vauxr-ai/vauxr/blob/docs/complete-api-reference/docs/site/api-transport-coverage.json)
records registration, evidence, consumer revisions and verification limits.

## Routes and authority

| Method/path | Status | Contract |
| --- | --- | --- |
| GET `/ws` | Shipping device transport | WebSocket upgrade, then device credential in JSON control frames |
| GET `/agent` | Shipping integration transport | WebSocket upgrade, then `agent.auth` within 10 seconds |
| POST `/api/offer` | Conditional WebRTC signaling | Device credential, active realtime wake, JSON SDP offer |

`/agent` is `cfg.agent.ws_path` (currently fixed by config construction).
`/api/offer` is `cfg.realtime.offer_path`, configurable with
`REALTIME_OFFER_PATH` or persisted realtime configuration. Only
`realtime.enabled` registers it; optional realtime dependencies must be installed.
Advertising/arming the device WebRTC path additionally requires a configured
`REALTIME_HOST` and compatible device policy. Disabled registration does **not**
produce a promised JSON API error: the static catch-all may handle that path.
Each GET registration also gets aiohttp's implicit HEAD route; HEAD is not a
second supported wire protocol. Both HTTP and WS listeners serve the same app
(default ports 8080 and 8765); enabled TLS adds a listener, not separate routes.

No query parameter authenticates a transport. Owner cookies do not substitute
for enrolled device or integration credentials. Credentials remain live-checked;
rotation/revocation can close sockets and stop retained media. Legacy shared
`DEVICE_TOKEN` is not authority. Use WSS/HTTPS at the configured authority when
TLS is configured. Owner-cookie middleware additionally checks exact origin,
authority and mutation CSRF when such cookies accompany a request.

The agent upgrade rejects any query string, duplicate Origin, an Origin other
than the configured owner origin, and insecure requests when that origin is
HTTPS (HTTP 403 before upgrade). A non-browser agent may omit Origin. Device
upgrade itself does not authenticate: successful 101 is not admission to audio.

## Device WebSocket: `/ws`

Text frames are JSON objects with string `type`. Unknown extra keys generally
are ignored, not a versioned schema negotiation. Malformed/non-object JSON or
non-string type returns `{"type":"error","code":"INVALID_MESSAGE","message":"Invalid JSON"}`.

First send `hello`, `voice.start` or `realtime.start` with `token` (string enrolled
device credential) and `device_id` (string matching its subject). `hello` is the
normal handshake. The principal and device identity bind to the connection;
subsequent messages may omit token/device_id. If supplied they must resolve to
the same live principal and identity. Another socket registering the device
supersedes this socket's output ownership. Permission failure sends error code
`UNAUTHORIZED` (no current credential) or `FORBIDDEN` (wrong operation/identity),
message `Access denied`, and closes the socket. Unknown string operations are
rejected by this authorization gate, not a reliable `UNKNOWN_MESSAGE` response.

### Client → server controls

All rows require the bound device identity. Fields below are in addition to `type`.

| Type | Fields, defaults and effect |
| --- | --- |
| `hello` | First-frame credential/identity as above; optional `caps: string[]` (non-strings ignored, default empty), `platform: string`, `fw_version: string`, output-rate hints below. Registers an idle device and returns server policy. |
| `voice.start` | Optional `name` (registration metadata), output-rate hints. Aborts old standard turn, clears capture, resolves speech settings, enters listening and returns `ready`. Ignored during active realtime. |
| `voice.end` | No fields. Ends standard capture and starts asynchronous processing; without listening returns `INVALID_STATE` / `Not in listening state`. Ignored during realtime. |
| `abort` | No fields. Clears standard capture/active output or stops realtime; no success acknowledgement. |
| `realtime.start` | Optional `name`, rate hints; physical cold wake requires `startup_id: integer` in 1…4294967295. `mode: "live"` explicitly selects browser Live subject to durable browser enrollment/policy; otherwise selected pipeline applies. Returns `ready` or browser Live `realtime.armed`. |
| `realtime.media_ready` | `startup_id: integer`, `next_seq: integer`; closes the WS pre-roll segment only when startup ID matches and next sequence is exact. Invalid types/stale IDs do not complete startup. |
| `realtime.pause` | Pause realtime microphone admission (warm quiet), no acknowledgement. |
| `realtime.resume` | Resume realtime microphone admission, no acknowledgement. |
| `realtime.stop` | Stop peer/startup and abort local turn, no acknowledgement. |
| `device.button` | Optional `button: string` (default `action`), `gesture: string` (default empty). Schedules configured button action using authenticated device identity; not an arbitrary webhook target. |

Permissions map respectively to `device.connect`, `device.audio`,
`device.self_control` and `device.button` in `auth/policy.py`. Owner and
integration credentials cannot impersonate a device here.

Rate hints: positive numeric `output_sample_rate`, falling back to `sample_rate`,
are converted to integer Hz; then persisted output rate, then platform defaults
apply. These are playback hints, **not** microphone-format negotiation.

### Server → device controls and audio

| Type | Payload |
| --- | --- |
| `hello` | `pipeline_mode` (selected mode or null for chat-only), `chat_only: boolean`, `voice_enabled: boolean`, `realtime: object` below |
| `ready` | No additional fields; capture/start accepted, not completed audio |
| `realtime.armed` | Browser Live armed; send SDP offer next |
| `realtime.ready` | Realtime pipeline ready; not a playback receipt |
| `speech.start` | Realtime detected speech |
| `transcript` | `text: string`, recognized user utterance |
| `audio.start` | Standard PCM output includes `sample_rate: integer` Hz; realtime control may omit it because media is on RTP |
| `audio.end` | `follow_up: boolean`; server's next-listen policy, not proof the listener heard all output |
| `device.control` | `command: string`, optional `params`; see below |
| `error` | `code: string`, `message: string` |

Disabled hello realtime policy is `{"enabled":false,"transport":"ws"}`.
Enabled policy has `enabled:true`, `transport:"webrtc"`, absolute same-origin
`offer_url`, `stun` URL, `startup:"ws_pcm_v1"`, and
`voice_source` (`live_voice_settings` or `speech_settings`), plus device policy
extras from `devices/settings.py`: `taper.t_idle1_ms: integer` (8000),
`taper.t_idle2_ms: integer` (180000), `vad.confidence: number` (0.6),
`vad.start_secs: number` (0.4), `vad.stop_secs: number` (2.0), and
`vad.min_volume: number` (0.1). Follow this policy rather than constructing
an offer URL from the ICE host. `VOICE_CONFLICT` can follow hello.

Binary frame format is **one type byte + two sequence bytes (unsigned big-endian)
+ raw PCM payload**. Input type `0x01` is signed 16-bit little-endian, 16 kHz mono
microphone PCM. Output `0x02` is TTS; `0x03` is proactive announcement PCM at the
advertised output sample rate. Sequence is 16-bit and wraps. Frames shorter than
three bytes or non-`0x01` device binary types are silently discarded; binary
before valid registration/current socket ownership closes the connection.
Standard capture buffers payloads only while listening, does not enforce input
sequence continuity and does not expose a transport-level backpressure protocol.
Do not describe the bounded realtime startup queue as a bound on standard capture.

Physical cold realtime startup sends ordered WS PCM starting at sequence zero
while constructing WebRTC. POST the offer with the same startup ID. Only after
the RTP sender is usable send `realtime.media_ready` with the next unsent WS
sequence, then stop WS pre-roll. The server drains that finite segment before
queued RTP, preserving order; setup has a 20-second timeout and a 15-second audio
budget, with paced replay at 1.15× and at most four seconds of burst credit.
Empty/odd-length PCM or discontinuous sequence fails startup. Warm re-wake on an
existing live peer can return `ready` without a new pre-roll segment. Stop,
disconnect or credential retirement tears down the associated startup/peer;
replacement wakes are identity-fenced from old cleanup. There is no replay or
resume cursor across reconnects: reconnect, authenticate and obtain policy again.

Other device errors include `VOICE_CONFLICT`, `SPEECH_UNAVAILABLE`,
`REALTIME_UNAVAILABLE`, `REALTIME_STARTUP_REQUIRED`, `REALTIME_STARTUP_SEQUENCE`,
`REALTIME_STARTUP_TIMEOUT`, `REALTIME_STARTUP_OVERFLOW`, `REALTIME_STARTUP_FAILED`,
`PIPELINE_ERROR`, `STT_ERROR`, `TTS_ERROR`, `NO_AGENT`, and `BACKEND_ERROR`.
Not every error closes the socket. Abort/cancel suppresses retired local output;
it is not an assertion that an external agent's action was cancelled.

`device.control` commands emitted by HTTP command handling are `set_volume`,
`mute`, `unmute`, `reboot`, `ota`; optional `params` is forwarded. OTA requires
`params.url` with HTTP/HTTPS URL. `set_barge_in` instead updates persisted
`barge_in` from boolean `params.enabled`, with no corresponding command frame.
Button actions can also emit configured control frames. See HTTP reference for
administrative request authority; a queued command is not a device execution receipt.

### Minimal standard capture example

Replace placeholders locally; do not paste credentials into shared logs. These
are source-checked frame examples, not executed live-device instructions.

```json
{"type":"hello","device_id":"DEVICE_ID","token":"DEVICE_CREDENTIAL","caps":[],"platform":"browser","output_sample_rate":24000}
```

After hello policy, send `{"type":"voice.start"}`, await `{"type":"ready"}`,
send binary frames such as header bytes `01 00 00` followed by valid PCM, then
`{"type":"voice.end"}`. Read transcript, output audio and `audio.end`. Send
`{"type":"abort"}` to stop local capture/output. There is no client-supplied
standard device turn ID in this wire contract.

## Integration WebSocket: `/agent`

Send `{"type":"agent.auth","token":"INTEGRATION_CREDENTIAL"}` within 10 seconds.
The credential must permit `agent.connect` and identify an existing agent.
Success is `{"type":"agent.ready","agentId":"AGENT_ID","name":"Agent name"}`.
Replacement authentication closes the previous connection for that agent.
Pre-auth non-auth messages return `UNAUTHORIZED`; timeout sends `AUTH_TIMEOUT`
and closes. Bad credential gives `UNAUTHORIZED`, wrong role/subject gives
`FORBIDDEN`, both with `Access denied` and close. Binary frames are ignored;
text exceeding 65536 characters closes. Invalid JSON (including duplicate keys)
returns `INVALID_MESSAGE`; JSON non-objects are ignored.

### Standard transcript exchange

Server sends:

```json
{"type":"agent.transcript","deviceId":"DEVICE_ID","sessionKey":"vauxr:DEVICE_ID","text":"What time is it?"}
```

Optional `deviceDisplayName: string` is validated stored display metadata, never
identity or a routing key; optional `uiContext: object` is companion context.
`text` may include untrusted companion context. Plugin responds with:

```json
{"type":"agent.response.delta","deviceId":"DEVICE_ID","runId":"RUN_ID","text":"It is noon."}
{"type":"agent.response.end","deviceId":"DEVICE_ID","runId":"RUN_ID"}
```

Or `agent.response.error` with the same string identifiers and `message` (default
`Agent error` if missing). Delta text must be string. The peer assigns `runId`;
the server binds its first response to an admitted transcript and retains a
bounded 256-origin history. Later frames correlate by connection, device and run.
Selected route, originating listener and text admission checks additionally fence
output. Missing IDs/text can be ignored; uncorrelated output after route change
returns `FORBIDDEN` without closing the shared integration. Recognized stale
output is discarded without cancelling unrelated devices or backend work.
Unknown authenticated operations instead fail authorization and close.

### Realtime backend exchange (internal integration contract)

Server sends `agent.realtime.request` with string `requestId` (32 hex characters),
`deviceId`, `session` (ephemeral scope), `operation`, and object `payload` (default
`{}`); optional stored `deviceDisplayName`. Plugin answers
`agent.realtime.result` with the same `requestId` and `deviceId`, and either
`result: object` or truthy `error` (normally string). Missing string requestId
returns `INVALID_MESSAGE`. Only the matching connection/device/pending request
can complete the future. Default request timeout is 30 seconds; consult uses
300 seconds. Local timeout/cancellation removes the pending future, **does not**
send backend cancellation. Check backend status before retrying an action.

| Operation | Payload | Result from current plugin |
| --- | --- | --- |
| `bootstrap` | `{}` | `instructions: string`, `messages: {role,content}[]`, `sessionId: string`; opens ephemeral scope on durable device conversation |
| `record` | `fragments: array` (max 64) | `recorded: integer`; records without dispatching actions |
| `consult` | `request: string` (max 32000 characters at plugin) | `text: string`; dispatches latest consultation and may execute tools |
| `release` | `{}` | `released:true`; drops ephemeral scope, not durable transcript |

Each record fragment has `id` matching `[a-zA-Z0-9_-]{1,100}`, `role` user or
assistant, `text: string` up to 16000 characters and optional `delivered: boolean`.
Undelivered assistant output is marked delivery-unconfirmed; record identity is
session + fragment ID. Non-bootstrap/release operations require a current scope.
Plugin deduplicates realtime request work by request identity and serializes
non-consult work per device; this is not an HTTP idempotency guarantee.

### Companion operations (internal integration contract)

Authenticated peers permitted `voice.respond` may send these operations, without
a per-turn lease. They are not general public plugin tool APIs.

| Type | Exact fields besides type | Reply |
| --- | --- | --- |
| `agent.ui.action` | `deviceId: string`, `requestId: string` (1–64 chars), `action: object` | `agent.ui.result` echoing requestId/deviceId, with `result` or `error` string |
| `agent.ui.name.update` | `deviceId: string` (1–128 chars), `requestId: string` (1–64), `resourceId: string`, `newValue` (validated by agent rename) | Same envelope, `result:{resource_id,name}` or `error:"Name update unavailable"` |

Extra keys/invalid fields on action are silently ignored; name-update validation
returns the error envelope. Action availability/target schemas belong to current
companion UI context and implementation, not a stable arbitrary-execution API.

The current plugin reconnects with exponential delay starting at one second,
capped at 30 seconds, and authenticates each new socket. This is consumer
behavior, not server-guaranteed delivery/replay. No network `agent.cancel`
operation is registered. The plugin type union includes `agent.device_state`,
but the inspected server does not emit it; do not depend on it.

## WebRTC signaling: POST `/api/offer`

Send JSON after a successful `realtime.start`. Authentication accepts an exact
`Authorization: Bearer DEVICE_CREDENTIAL` or a body `token: string`. If both are
present they must authenticate to the same principal. Device identity must match
the credential's subject (`realtime.offer` permission); owner/integration tokens
are insufficient. Body keys:

| Key | Type / requirement |
| --- | --- |
| `sdp` | Required string SDP offer generated by the actual local peer; wrapper checks presence, underlying WebRTC library validates SDP |
| `type` | Required SDP type; use string `offer`; wrapper checks presence |
| `device_id` | Required enrolled device subject string |
| `token` | Optional only when valid Authorization header is supplied |
| `startup_id` | Integer matching armed physical cold wake; not required for explicit browser Live path |
| `pc_id` | Must be absent/null: caller-selected peer handles forbidden |
| `restart_pc` | Must be absent/false: peer restart selection forbidden |

Illustrative request shape (SDP placeholder is deliberately **not** valid SDP):

```json
{"sdp":"<SDP_GENERATED_BY_LOCAL_PEER>","type":"offer","device_id":"DEVICE_ID","token":"DEVICE_CREDENTIAL","startup_id":1}
```

Success `200` passes through the Pipecat SmallWebRTC answer object: `sdp: string`,
`type:"answer"`, `pc_id: string`. Treat the handle as opaque; it does not grant
re-offer authority. No public trickle-ICE, peer PATCH, DELETE or cancellation HTTP
endpoint is registered here. Media then uses negotiated WebRTC RTP/DTLS-SRTP;
device control stays on `/ws`, not SSE. A successful answer alone is not proof
that ICE, authentication-dependent pipeline startup or audible playback completed.

| Status | JSON `{error: string}` conditions |
| --- | --- |
| 400 | `Invalid JSON`, `Missing sdp/type` |
| 401 | `unauthorized` (credential invalid, integration dependency missing or credential retired during admission) |
| 403 | `forbidden` (wrong device/role, conflicting tokens, peer selection/restart), or `No active realtime session` |
| 409 | Chat-only/voice configuration conflict string; `Realtime wake superseded or already connected` |
| 500 | `realtime offer failed`, `No SDP answer` |
| 503 | `transport_teardown_unavailable` |

Malformed SDP is not guaranteed a 400: failures inside offer construction are
caught as 500 and abort that wake. Repeated/concurrent offers are fenced by wake
identity; a stale offer cannot terminate its replacement. Revocation retains
teardown authority until media cleanup completes.

## Streaming classification and limits

There is **no registered SSE endpoint or HTTP streaming-audio endpoint** in this
source revision. `StreamResponse` type annotations alone do not imply SSE.
Firmware artifact download is a binary file stream, not an audio protocol (REST
reference owns it). Wyoming TCP audio is a server/provider dependency, not a
registered client-facing API route. No deprecated transport alias is registered.
This page documents shipping routes plus the explicitly conditional signaling
route, not a redesigned transport or generic plugin tool catalogue.
