# Speech, settings and browser features

[HTTP conventions](api.md). Revision `25469df63362853a2a4daf18cc0a115c6626b6bf`.
All routes on this page require an owner session. All mutation examples also
require same-origin CSRF headers. Unless specified, success is JSON 200.

## Speech selection

### GET /api/speech and PATCH /api/speech
### GET /api/devices/{device_id}/speech and PATCH /api/devices/{device_id}/speech
### GET /api/agents/{agent_id}/speech and PATCH /api/agents/{agent_id}/speech

Owner `speech.configure`. GET returns SpeechView; PATCH applies an object patch
then returns SpeechView. Global route edits defaults, agent route edits policy
and inherited selection, device route edits overrides. An absent agent is 404;
device-specific speech settings do not require a connected inventory entry.
Omitted selection fields stay unchanged; IDs must come from registry GET.

| Patch field | Type / rule |
| --- | --- |
| stt_backend | String registry ID, kind stt |
| tts_backend | String registry ID, kind tts |
| voices | Object mapping TTS backend IDs to their registered voice strings |
| mode | `standard` or `realtime` |
| realtime_backend | String registry ID, kind realtime |
| realtime_voice | String belonging to selected realtime backend |
| agent_id | Device route only; registered agent ID or null (clear assignment) |
| reset | Boolean; true clears device overrides, not global/agent selections |
| enabled_modes | Agent only; unique array of `standard`, `realtime`; [] means chat-only |
| default_mode | Agent only; one of enabled_modes, or null for chat-only |

Device selection fields and individual voices may be null to remove overrides.
Agent policy fields are not device/global fields. Unknown fields are rejected.
Example device patch `{"agent_id":"<agent-id>","reset":true}`. Example agent
chat-only patch `{"enabled_modes":[]}` automatically sets default_mode null if
omitted. Active voice, capability and fallback-policy conflicts return 409;
invalid selections/types return 400. These handlers do not give all storage
exceptions a dedicated stable status (owner middleware may produce 503).

SpeechView shape:

- `voice:{mode:string|null,realtime_backend,realtime_model,realtime_voice}`.
- `realtime:{voices:string[],configured:boolean}`. Configured is not proof of
  provider reachability or successful media.
- `defaults`: inherited selection; `overrides`: device selection object.
- `effective:{stt_backend,tts_backend,voice_id}|null`, `error:string|null`.
- `policy:{agents:[{id,name}],fallback_agent,agent_id,assigned,configured,
  chat_only,integration_modes,enabled_modes,default_mode,available_modes,
  capabilities_known,conflict}`. Flags are boolean, modes arrays, conflict nullable.
- `backends:[{id,kind,adapter,model,voices,readiness}]`; host/port are withheld here.
  Readiness is a bounded probe string, not a guarantee. Chat-only reports disabled.

### POST /api/speech/preview — browser-bound, conditional

Requires owner plus `X-Vauxr-Browser` for this connected, enrolled browser.
Exact JSON `{device_id:string,tts_backend:string,voice:string}` within 4096 bytes
and 5 seconds; no queries. ID must match proof subject; backend must be TTS and
voice registered. No arbitrary preview text: server speaks its fixed sentence.
Example `{"device_id":"<this-browser-id>","tts_backend":"piper","voice":"<registered-voice>"}`.
Returns audio/wav, mono signed 16-bit/24 kHz, no-store; collection limited to
30 seconds of audio and 20 seconds wall time. 403 invalid browser proof;
409 disconnected/chat-only; 400 selection/body; 503 synthesis unavailable.
This does not save the previewed voice or send audio to a physical device.

## Provider connection profiles

These routes reject query parameters. Error object is `{error:string}`:
400 invalid/unavailable settings, 409 active-voice conflict, 503 storage
unavailable; owner 401/403 still apply. Error messages deliberately redact values.

### GET /api/settings/providers

Returns `{version:1,profiles:Profile[],speech_auth:{[backend_id]:profile_id},
capabilities:{providers:["ollama","openai"],speech_profile_adapters:["openai"],
realtime_auth:"environment",discovery:string,openai_environment_configured:boolean}}`.
Profile fields: `id,name,provider,endpoint,model,auth,credential_configured:boolean`.
Credentials are never returned.

### PUT /api/settings/providers/profiles/{profile_id}

Full profile fields required: `{name,provider,endpoint,model,auth}`. Optional
`credential:string` replaces inline credential; `clear_credential:boolean`
(default false) clears it. Omission preserves secret. Replacement and clear
cannot both be used. Replacement is nonempty, at most 8192 characters, no controls.
IDs match `[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}`. Name/model are 1–200 characters,
no controls. At most 32 profiles. Existing provider type cannot change.
`provider` is ollama/openai; auth is none/environment/inline. Ollama requires
none; endpoint is HTTP(S), at most 500 characters, no credentials/query/fragment.
OpenAI requires exact `https://api.openai.com` and environment or inline auth.
Returns the complete masked provider view. Example (no paid request made):

```json
{"name":"Local model","provider":"ollama","endpoint":"http://ollama.lan:11434","model":"<installed-model>","auth":"none"}
```

### POST /api/settings/providers/profiles/{profile_id}/discover

No body consumed. Metadata-only provider model discovery, 5-second limit,
no redirects, response capped at 256 KiB. Returns
`{models:string[],source:"ollama-tags"|"openai-models"}` (at most 256 sorted IDs).
Does not save a selection or run inference; unavailable discovery returns 400.

### PUT /api/settings/providers/speech-auth

Exactly `{backend_id:string,profile_id:string|null}`. Only OpenAI Standard TTS
adapter accepts shared profile authentication. Null removes binding and uses
environment authentication. Returns complete provider view. Example
`{"backend_id":"openai-tts","profile_id":null}`. This is not realtime auth setup.

## Speech deployment registry

### GET /api/settings/speech

Returns `{version:1,providers:Backend[]}` with owner-visible hosts and ports.
Backend: `{id,kind,adapter,model,host,port,voices}`. Strings except integer port
and array voices. Selection APIs use these opaque deployment IDs.

### PUT /api/settings/speech/{backend_id}

Full Backend object; id must equal path. Existing id/kind/adapter cannot change.
New IDs use the profile ID syntax above. Port 1–65535; host nonempty up to 253,
no whitespace/path/userinfo/query characters; model/voice strings nonempty,
maximum 200 without controls; voice IDs unique. `voices` defaults [] but is
required nonempty for tts/realtime. Allowed kind→adapter:
stt→wyoming/whisper/parakeet-v3; tts→wyoming/piper/kokoro/openai;
realtime→openai-live. OpenAI and openai-live require api.openai.com:443.
No credential fields. Registry maximum 32 deployments. Returns registry;
400 validation or broken selection references; 409 changing an active deployment.
Example:

```json
{"id":"patio-tts","kind":"tts","adapter":"piper","model":"<installed-model>","host":"piper.lan","port":10200,"voices":["<installed-voice>"]}
```

### POST /api/settings/speech/{backend_id}/discover

No body consumed. Returns `{models:string[],voices:string[],source:string,
message?:string}`. Wyoming uses describe (3-second timeout, bounded response),
source `wyoming-describe`. OpenAI adapters return configured voices and empty
models, source `configured`, with manual-entry message. Does not synthesize,
save, or prove model availability. Invalid/unavailable is 400.

## Plugin distribution and declared settings

GET/PUT below are owner-only; no query strings. Download catalog does not imply
a plugin has registered a settings schema.

| Method/path | Request | Success |
| --- | --- | --- |
| GET /api/plugins | None | Catalog array: `{id,name,distribution}` plus archive `revision,source,sha256,download,filename` |
| GET /api/plugins/openclaw/package | None | application/gzip attachment, no-store, nosniff |
| GET /api/settings/plugins | None | `{plugins:[{id,name,fields,values,secrets}]}` |
| PUT /api/settings/plugins/{plugin_id} | Object below | Complete plugin settings view |

Catalog entries include builtin Support, bundled LangChain, archived OpenClaw.
The archive has a pinned independent revision and checksum; it is not a package
manager install endpoint. Distribution query error: 400 `{error:"invalid request"}`.

Declared fields expose `key,label,type,required,default,choices,min,max`. Types:
string/integer/boolean/secret. `values` excludes secrets; `secrets` maps secret
keys to configured booleans. PUT allows only optional `values:{}`, `secrets:{}`,
`clear_secrets:[]`, defaulting to those empty values. Omitted fields persist.
Only loaded, registered keys can be saved. Strings max 4096, no controls;
integers honor declared bounds; choices/required honor schema. Secret replacement
must be nonempty; cannot also clear it. Example generic shape
`{"values":{"<declared-boolean-key>":true}}` (replace key using GET schema).
400 undeclared/type/required/clear error; 503 storage unavailable.

## Local agents and Support runtime

Owner, no queries. POST JSON max 4096 bytes/10 seconds, exact fields.

| Method/path | Request | Success |
| --- | --- | --- |
| GET /api/local-agents | None | `{definitions:[...]}` registered definition catalog |
| POST /api/local-agents | `{definition_id:string,name:string}` | 201 registered Agent object |
| GET /api/local-agents/{agent_id}/runtime | None | RuntimeView |
| POST /api/local-agents/{agent_id}/runtime | Runtime body below | RuntimeView |
| GET /api/support | None | RuntimeView for built-in Support |
| POST /api/support | Runtime body below | RuntimeView for built-in Support |

Definition objects contain `id,name,framework,implementation,supported_modes`;
strings except `supported_modes:string[]` (currently `["standard"]`).
Creation only supports definitions with framework langchain. Name is trimmed
1–120 characters without display control characters. Returned Agent object is
`{id,name,type:"langchain",active:false,createdAt,builtin:null,integration:false,definition_id}`
(strings except boolean flags and nullable builtin), not a credential. Runtime operations require
existing LangChain agent (404). Runtime body is either exactly
`{profile_id:string}` referencing a saved profile, or exactly
`{provider:"ollama",endpoint:string,model:string}`. Model trimmed 1–120, no
controls, no `cloud` substring. Endpoint must be HTTP, explicit port,
private/loopback IP or .local/.lan hostname, no userinfo/query/fragment and no
path except `/`. Three empty strings reset legacy runtime setup.
RuntimeView is the stored body plus status `configured|setup_needed|unsupported`;
a referenced non-Ollama profile is unsupported. Example
`{"provider":"ollama","endpoint":"http://ollama.lan:11434","model":"<installed-model>"}`.
400 validation; 503 storage unavailable. Deletion is in [control reference](api-http-control.md).

## Static interface authoring

Owner, no queries. POST JSON at most 128 KiB/10 seconds; no duplicate keys.

| Method/path | Body | Success |
| --- | --- | --- |
| GET /api/interface | None | `{title,html,css,revision,published:boolean}` |
| POST /api/interface | Exactly `{title,html,css,revision}` | Updated source object |
| POST /api/interface/preview | Exactly `{title,html,css}` | `{document:string}` rendered HTML |

Title is nonempty trimmed string ≤120 without controls, HTML ≤20000 and CSS
≤12000 characters. Save requires exact revision from GET for optimistic
concurrency. Preview does not save. HTML is allowlist-sanitized; CSP forbids
scripts/network/resources/forms. This is static content, not a code runner.
409 invalid source/stale revision; 503 storage unavailable. Example preview:
`{"title":"Patio","html":"<p>Welcome</p>","css":"p { color: green; }"}`.

## Browser companion: POST /api/companion/{action} — internal portal contract

Owner plus exact `X-Vauxr-Browser` device proof of kind browser. No queries;
JSON duplicate keys rejected, 4096 bytes/10 seconds (chat 12288). Every body
contains top-level `browser_id` equal to proof subject, plus exact fields below:

| Action | Other fields | Success |
| --- | --- | --- |
| events | None | `{events:[event objects]}` for this browser |
| ack | `event_id:string,applied:boolean` | `{ok:true}` |
| context | `route,resource_type,resource_id,setting_key,current_value` | Same context plus `revision:string` |
| select | `agent_id:string` | `{agent_id}` and clears transient context |
| chat | `request_id:string,text:string` | `{request_id,text:string}` reply |

Context route is agents/devices/settings. To publish no selection, all resource
fields are null. Selected context only supports route agents, resource_type
agent, setting_key name, registered resource_id and current_value equal to its
current safe display name. Context expires after one hour. Events are transient
correlated navigate/highlight requests, include event_id and action fields;
acknowledgement is not authorization for unrelated actions. Chat request_id is
32 lowercase hex; text trimmed 1–2000. Chat is conditional on combined app state,
not available in standalone make_http_app. Example context:
`{"browser_id":"<this-browser-id>","route":"settings","resource_type":null,"resource_id":null,"setting_key":null,"current_value":null}`.
401 absent owner; 403 invalid browser proof; 404 unknown action; 409 invalid/stale
context, voice conflict or chat conflict; 503 save/text transport unavailable.
