# Devices, agents, webhooks and firmware

[HTTP conventions and authentication](api.md). Source revision:
`25469df63362853a2a4daf18cc0a115c6626b6bf`. All paths below are registered on the
portal. Unless stated otherwise success is 200 and authentication is owner-only.
Policy failures 401/403 apply throughout.

## Devices

### GET /api/devices

Owner or integration (`devices.list`). No request body. Returns an array of
Device objects: `id`, `name`, `state`, `lastSeen` (UTC ISO timestamp), `config`;
optional `platform`, `fw_version`, and `chat_only:true`. Config exposes
`pipeline_mode` (`standard|realtime`, null for chat-only), `voice_mode`
(`websocket|webrtc|realtime`, null for chat-only), and configured optional `name`,
`voice`, `follow_up_mode`, `barge_in`, `output_sample_rate`, `button_actions`.
It is connected inventory, not a permanent enrollment catalog.

### GET /api/devices/{device_id}/metrics
### GET /api/devices/{device_id}/metrics/{window}

Owner (`device.metrics`). Path window overrides query `window`; default `24h`.
Allowed `1h|24h|7d|30d`, bucket seconds respectively 60/3600/21600/86400.
Returns `{device_id,window,bucket_seconds,observed_since,retention_days:30,
metrics:[{key,label,definition}],totals,buckets:[{start,...counters}]}`.
Counters are nonnegative integers keyed by `stt_transcriptions`, `tts_syntheses`,
`realtime_user_turns`, `realtime_assistant_turns`. Counts are content-free usage,
not confirmation of playback. 400 invalid window; 404 unknown device.
Example: `GET /api/devices/<device-id>/metrics?window=1h`.

### PATCH /api/devices/{device_id}

Owner (`device.configure`). Object patch; omitted fields remain unchanged,
unknown fields are ignored. Returns the updated Device object. Fields:

| Field | Type / accepted value |
| --- | --- |
| name | String (HTTP handler does not enforce display-name length/controls) |
| voice | Boolean |
| voice_mode | `websocket`, `webrtc`, `realtime` |
| transport_mode | `websocket`, `webrtc` (legacy separate selector) |
| pipeline_mode | `standard`, `realtime` (legacy separate selector) |
| follow_up_mode | `auto`, `always`, `never` |
| barge_in | Boolean |
| output_sample_rate | Integer 16000, 22050, 24000, 44100, or null |
| button_actions | Object keyed by `double_press`, `triple_press`, `long_press` |

Each gesture value is `{kind:"none"}` (remove), `{kind:"prompt"|"announce",text:
"nonempty text"}`, `{kind:"webhook",webhook_id:"<webhook-id>"}`, or
`{kind:"command",command:"set_volume"|"mute"|"unmute"|"reboot"}`. `set_volume`
also requires integer `volume:0..100`. `{}` clears all button actions.
Combined voice_mode takes precedence over separate selectors: websocket forces
standard pipeline and barge_in false; webrtc selects standard; realtime selects
WebRTC/realtime. Any resulting websocket transport forces barge_in false.
Fresh transport defaults to WebRTC; pipeline to standard; follow-up falls back to
auto; barge-in defaults enabled unless websocket policy disables it.
The display metadata loader separately expects ≤128 UTF-16 units without controls;
the HTTP patch currently only checks that name is a string. Null sample rate
removes the override. 400 malformed JSON/type/value; 404 missing device; 409 voice-policy conflict.
Example body: `{"name":"Patio speaker","follow_up_mode":"auto"}`.

### POST /api/devices/{device_id}/announce

Owner or integration (`device.announce`). Body `{"text":"Dinner is ready"}`;
text must be a nonempty string. Returns `{"ok":true}` after synthesis/delivery
attempt, not verified audible playback. 400 invalid JSON/missing text;
404 missing device; 409 listening/processing device or chat-only selection;
503 selected speech provider unavailable.

### POST /api/devices/{device_id}/command

Owner or integration (`device.control`; OTA additionally `firmware.initiate`).
Body `{command:string,params?:any}`. Commands: `set_volume`, `mute`, `unmute`,
`reboot`, `ota`, `set_barge_in`. OTA requires `params:{url:string}` beginning
`http://` or `https://`. `set_barge_in` requires `params:{enabled:boolean}`;
it persists policy and returns `{ok:true,barge_in:boolean}`. Other commands
return `{ok:true}` after sending a control frame; arbitrary params are passed
through, not validated by this handler (device support determines behavior).
Example: `{"command":"set_volume","params":{"volume":40}}`.
400 missing/unknown command, invalid OTA URL or enabled value; 404 missing device.
A success is not an OTA completion or reboot acknowledgement.

## Agents

### GET /api/agents

Owner (`agent.list`). Returns array of `{id,definition_id,name,chat_only,
integration_metadata,type,active,createdAt,...avatar metadata,builtin?}`.
`active` and `chat_only` are boolean, `createdAt` a timestamp string; `definition_id`
is string or null. Avatar metadata is optional `avatar_url:string,avatar_revision:string`.
`integration_metadata` is `{framework:"openclaw"|"langchain",configuration:"builtin"|"local"|"operator-managed"|"enrolled"|"registered"}`, not credentials. Use instance `id` in URLs.

### PATCH /api/agents/{agent_id}

Owner (`agent.configure`). Exactly `{name:string}`; trimmed 1–200 characters,
no control characters. Example `{"name":"Patio helper"}`. Returns `{id,name}`.
400 invalid JSON/extra fields/name; 404 missing agent; 503 save unavailable
(refresh before retrying because persistence may be uncertain).

### POST /api/agents/{agent_id}/activate

Owner. No body required. Sets fallback/default agent and returns `{ok:true}`.
404 missing agent; 409 incompatible speech fallback policy.

### DELETE /api/agents/{agent_id}
### DELETE /api/local-agents/{agent_id}

Owner. No body required. Returns `{removed:true}`. The local-agents alias requires
a LangChain instance (404 otherwise). 404 missing; 409 active default agent,
non-removable bundled agent, or voice conflict. Choose another default first.
503 partial removal/storage/transport retirement failure: refresh before retrying.
Removal retires integration credentials and detaches routing/voice overrides.

### GET /api/agents/{agent_id}/avatar
### POST /api/agents/{agent_id}/avatar

Owner (`agent.configure` even for GET). GET returns PNG bytes with no-store and
nosniff, or 404 (missing image may be framework text). POST is raw PNG/JPEG/WebP
bytes, not JSON/multipart, up to 2 MiB and 2048×2048, within 10 seconds. Returns
`{avatar_url:string,avatar_revision:string}`; 400 invalid image/timeout; 413 oversized body; 404 missing
agent; 503 storage/image library unavailable. Source authority for metadata:
`src/vauxr/agents/avatars.py:metadata`.

### POST /api/agents — legacy, unimplemented
### POST /api/agents/{agent_id}/rotate — legacy, unimplemented

Owner only. Authorized calls return 501 `{error:"operation not implemented"}`.
Use local-agent creation or integration enrollment; use lifecycle v1 to rotate.
These are registered reservations, not credential creation APIs.

## Webhooks

All routes require owner (`webhook.configure`). Public Hook schema:
`{id:string,name:string,has_url:boolean,has_authorization:boolean,has_body:boolean}`.
No response exposes authorization. This API configures household actions; there
is no HTTP webhook-execution route here.

| Method/path | Request | Success | Additional failures |
| --- | --- | --- | --- |
| GET /api/webhooks | None | Hook[] | — |
| GET /api/webhooks/{webhook_id} | None | Hook plus `url:string,body:object|null`, no-store | 404 |
| POST /api/webhooks | Required `name,url`; optional `authorization,body` | 201 Hook | 400 |
| PATCH /api/webhooks/{webhook_id} | Any of those fields; omitted preserved | Hook | 400,404 |
| DELETE /api/webhooks/{webhook_id} | None | `{ok:true}` | 404 |
| POST /api/webhooks/{webhook_id}/duplicate | None | 201 Hook, unique copy name | 404 |

Names/URLs must be nonempty trimmed strings. URL validation allows HTTP(S),
requires a nonempty network location; this handler does not reject URL userinfo. Authorization is a string; null or empty
clears it, omission preserves on PATCH. Body is an object, JSON object string,
null or empty string (clear); not a general scalar/array. Example create:
`{"name":"Patio light","url":"https://automation.example.invalid/light",
"body":{"on":true}}`. Duplicate copies the stored secret as well as config.
400 errors are `{error:string}`. Storage exceptions are not given a dedicated
stable error contract by these handlers.

## Firmware

### GET /firmware/{filename}

Owner or authenticated device (`firmware.read`), not integration. Filename must
match `[A-Za-z0-9._-]+\.bin` and exist directly in the firmware directory.
Returns binary attachment, content type application/octet-stream; 404
`{error:"not found"}` for invalid/missing path. No upload route is implemented.

### POST /api/firmware-delivery/{filename}

Owner or integration (`firmware.delivery_mint`, as implemented in policy).
No body required. 201 `{url:string,expires_in:120}`; 404 missing/unsafe file;
503 `{error:"delivery capacity reached"}` at 128 outstanding capabilities.
The URL is a bearer capability: keep it private. Example filename `device.bin`.

### GET /firmware-delivery/{token}/{filename}

Legacy-device compatibility capability download, no session/bearer header needed.
Returns raw application/octet-stream attachment. Single use, 120 seconds,
process-local (restart invalidates), no HEAD. Wrong filename, expired/used/missing
capability or file all yield 404 `{error:"not found"}`. Even mismatch or interrupted
transfer spends the capability. No redirects or conditional download semantics.
