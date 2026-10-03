# Persistent configuration

The following files in `DATA_DIR` are authoritative. Missing files initialize a
fresh configuration. Existing malformed files fail startup with a logged path,
field and correction, without including secret values. Manual edits take effect
on restart; management API edits apply to subsequent turns.

| File | Owns |
| --- | --- |
| `config.json` | General server settings, Direct connection URL, WebRTC policy and pairing prompts |
| `speech.json` | STT, TTS and realtime provider registry, global selections and per-device overrides |
| `agents.json` | Agent/integration routing metadata and the sole active-agent selection |
| `authz.json` | Scoped credential verifiers, owner authorization and enrollment/lifecycle proofs |
| `devices.json` | Device names, transport, follow-up, barge-in, output rate and button actions |

Webhooks retain their independent `webhooks.json` store. The private OpenClaw
Direct signing identity remains in `vauxr-identity.json`; it is not a routing
selection or a Vauxr client credential. TLS certificate/key and model-cache
files retain their separate documented deployment locations. Lock files hold no
configuration.

## Server settings and pairing

`config.json` contains optional `server`, `openclaw`, `realtime` and `pairing`
objects. A first server startup writes defaults seeded from deployment environment
variables. Once present, file settings take precedence. Absent settings use the
corresponding environment value or built-in default.

- `server`: `ws_port`, `http_port` (1–65535), `log_level` (debug/info/warning/error/critical),
  `streaming_tts_idle_pause_ms` (nonnegative), optional `device_id_length` (1–64).
- `openclaw`: `url`. `OPENCLAW_TOKEN` remains a deployment secret and is not written
  into this file.
- `realtime`: `enabled`, `esp32_mode` (booleans), `host`, `stun_url`, `offer_path`
  (strings) and `output_gain_db` (finite number).
- `pairing.prompts`: `intro` (1–400 characters) and `code` (1–200 characters).
  `code` must contain `{code}` exactly once; `intro` must not contain it.

The pairing management API updates `pairing.prompts` without replacing other
sections. Agent activation never writes this file.

## Speech providers and selections

`speech.json` has four required fields: `version: 1`, `providers`, `defaults` and
`devices`. Each provider has a unique `id`, `kind` (`stt`, `tts`, `realtime`),
`adapter`, `model`, `host`, `port`, and `voices` for TTS/realtime. Credentials are
not provider fields; OpenAI credentials remain deployment secrets. The shipped
OpenAI Live adapter requires its official `api.openai.com:443` endpoint.

New installs include Whisper, Piper, Parakeet v3, Kokoro, OpenAI TTS and OpenAI
Live, even if a service or API key is unavailable. Whisper and Piper are the
initial defaults. Their initial endpoints/voice come from `STT_URL`, `TTS_URL`
and `TTS_VOICE` (or the corresponding legacy deployment environment variables).
Configure the registry's model, endpoint and voice labels for the services you
actually run. Unavailable services never cause an automatic provider fallback.
The generic Wyoming adapter supports additional configured deployments.

Defaults contain `mode` (`standard` or `realtime`), `stt_backend`, `tts_backend`
and `voices` (TTS provider ID → voice). `realtime_backend` selects a realtime
registry ID, and `realtime_voice` must belong to that provider. The runtime obtains
the model from the selected registry entry. A realtime selection requires a
realtime provider; custom registries can omit realtime when all selections use
Standard mode.

`devices` maps stable device IDs to partial overrides of those settings. Missing
fields inherit global defaults. The speech management API accepts those selection
fields; a per-device `null` removes an override. The device API's `pipeline_mode`
and `voice_mode` controls also write the speech `mode` override, rather than
persisting another pipeline selection in `devices.json`. Transport stays in
`devices.json`. Provider selection and editing remain separate from readiness
checks and service/model provisioning.

## Agents and enrollment

```json
{
  "version": 1,
  "agents": [],
  "active_agent": ""
}
```

Agent records contain `id`, `name`, `type`, `createdAt`, and optional boolean
`builtin` and `integration` flags. OpenClaw Direct is a persisted builtin record
with ID/type `openclaw-direct`. An empty active ID means no agent is selected.
Activation replaces this single selection; retiring the selected integration
clears it without selecting another agent automatically.

Completed integration enrollment durably registers metadata in `agents.json`.
ACK retries are idempotent, including after a metadata-write failure. Immutable
request bindings stay with enrollment proofs in `authz.json`; they are not a
second routing registry. Credential validity still gates routing immediately,
including when a credential is revoked or a storage write fails.

## Device IDs

New IDs are `dev_` plus a prefix of the public-key SHA-256 fingerprint.
`DEVICE_ID_LENGTH` controls the suffix length and defaults to 16 hexadecimal
characters; an explicit `server.device_id_length` takes precedence. Values must
be integers from 1 to 64. Existing durable key bindings retain their exact ID,
regardless of its length or later changes to this setting. Prefix collisions
cannot claim another key's identity.

Firmware and the browser retain the entire server-assigned ID with the key and
credential. Clients treat it as an opaque identifier rather than deriving it
from the key. Firmware can still read deployed version-1 journals, preserving
their original full fingerprint IDs; new journal writes use version 2.

## Obsolete configuration

There is no migration or preservation of legacy configuration. Startup removes
`channels.json`, `speech-providers.json`, `speech-settings.json` and
`pairing-prompts.json` after validating the current configuration. Legacy
`agents.json` and `config.json` schemas, including `openclawDirectActive`, must
be explicitly reconfigured. Integration proofs containing a legacy active-agent
field also require explicit reconfiguration. Missing enrolled routing metadata
produces an error instead of silently reconstructing a registry from proofs.
