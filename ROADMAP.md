# Vauxr Roadmap

---

## Planned

Features grouped by theme. No ordering assigned.

### Conversation Quality
- ~~**Follow-up mode** — server sends `follow_up` flag; device stays in listening state automatically after a response~~ ✅
- ~~**Interruption** — server `abort` + per-device `barge_in` config~~ ✅
- **Response sanitization for voice** — strip emojis, code blocks, markdown formatting, URLs, and other non-spoken content from LLM output before TTS; catches what voice formatting prompts miss so the device never reads aloud ` ```python ` or 🔥
- ~~**Streaming TTS via idle-pause detection** — flush buffered assistant text to Piper whenever the delta stream goes idle (default 400ms) so the device starts speaking while the agent is still thinking or running tools, instead of waiting for the full reply~~ ✅

### Device Management
- **Scoped authentication initiative [#44](https://github.com/vauxr-ai/vauxr/issues/44)** — implemented server/browser groundwork with release acceptance still open. [Owner v1](docs/authz/owner-v1.md) uses explicit console claim, generated-token save acknowledgement and separate cookie login; no auth environment variable is required. Optional `OPERATOR_TOKEN` is an authoritative owner-login override, never a device or integration bearer.
- **Enrollment and access management** — [enrollment v1](docs/authz/enrollment-v1.md), [browser v1](docs/authz/browser-v1.md) and [lifecycle v1](docs/authz/lifecycle-v1.md) define separate scoped identities, physical matching-code approval, rotation/save ACK, revocation and explicit same-key recovery. Existing device and speech configuration remains; unrelated legacy identities require reviewed settings transfer.
- **Breaking migration [#52](https://github.com/vauxr-ai/vauxr/issues/52)** — follow the [installation, backup, rollback and reconnect guide](docs/authz/migration-52.md). `DEVICE_TOKEN` and legacy channel tokens grant no access; there is no compatibility window. The guide pins the unmerged server/plugin dependencies and records the missing firmware artifact and physical acceptance gates in [#53](docs/authz/combined-53.md).

### Home Assistant Integration
- Home Assistant and Matter are separate follow-ups, not scoped-auth release requirements.
- **Vauxr STT/TTS providers for HA** — HA sees stable "Vauxr STT" and "Vauxr TTS" entities that speak the Vauxr WS protocol under the hood. HA users can route their voice pipeline through Vauxr without ever exposing Whisper/Piper TCP ports directly. Distinct from the firmware ROADMAP's HA event forwarder (`vauxr.wake` etc. to `/api/events`) and from the existing webhook dispatcher.

### Provider Abstraction
- **Bounded configured Wyoming selection** — global defaults and per-device inheritance/overrides for STT, TTS and model-scoped voices; immutable turn snapshots and management UI. See [configuration and limitations](docs/speech-settings.md). This does not implement arbitrary provider plugins or model provisioning.
- **STTProvider / TTSProvider extension system** — pluggable provider interface so Whisper and Piper become one option among many. Swap in Deepgram, ElevenLabs, Groq Whisper, Coqui, or any other STT/TTS backend without touching device firmware or the WS protocol. Keeps the device-facing protocol stable while the backend evolves.

### Device Context & Voice Formatting
- ~~**Server-side device registry** (`devices.json` keyed by `device_id`, fields: `name`, `voice`, plus follow-up, barge-in, button actions)~~ ✅
- ~~**Session preamble injection** — on first turn of each session, server prepends hidden context to `chat.send` with device name and voice formatting rules (no emojis, no markdown, concise spoken sentences)~~ ✅ *(via `vauxr-openclaw` channel plugin's `voiceSystemPrompt`)*

### Transcription Accuracy
- **Conversation context for Whisper** — pass recent conversation history as an initial prompt to the Whisper API (`initial_prompt` field); primes the model with relevant vocabulary, proper nouns, and topic context from the current session, improving accuracy especially for domain-specific terms and follow-up questions

### Multi-Device & Proximity Detection
- **Wake word dedup** — when multiple devices hear the wake word simultaneously, server arbitrates: devices include a confidence score with the wake event, server holds a ~500ms dedup window, highest-confidence device wins (closest device naturally tends to win), losers receive a `cancel` frame to abort listening; prevents duplicate STT submissions and overlapping spoken responses

### Server-Initiated Control
- **Audio stream playback** — server sends a `device.play` control frame over WS containing a URL; device connects to the URL and streams + plays the audio as it downloads; enables music playback, internet radio, audio clips, or any audio source reachable by the device
- **Stop playback** — server sends a `device.stop` control frame to interrupt any currently playing audio (TTS or stream)
- ~~**Push TTS / announce** — `POST /api/devices/{id}/announce` synthesizes text via Piper and streams as `0x03` push audio frames to device; enables cron jobs, heartbeats, and proactive agent alerts to speak through the device~~ ✅
- ~~**Device control from OpenClaw** — `POST /api/devices/{id}/command` sends a `device.control` JSON frame (e.g. `set_volume`, `mute`, `reboot`); enables voice commands like "set the volume to 10" to actually change device state~~ ✅
- ~~**OTA firmware updates** — `device.control` `ota` with `params.url`; images served from `DATA_DIR/firmware/<platform>.bin`.~~ ✅
- **Device queries / telemetry** — bidirectional: server can request data from the device and await a response (e.g. "what's your battery level?"); device responds with a `device.response` frame; server surfaces the answer back to OpenClaw

### OpenClaw Channel Plugin (`vauxr-openclaw`)
- ~~Optional plugin for deeper OpenClaw integration~~ ✅
- ~~**Relay mode**: plugin opens outbound WS from local OpenClaw to Vauxr — no port forwarding needed~~ ✅

Plugin auth coordination is tracked in [plugin #36 / PR37](https://github.com/vauxr-ai/vauxr-openclaw/pull/37) against [integration v1](docs/authz/integration-v1.md). It provides owner-approved integration setup, protected credential persistence and scoped physical-device pairing. See the [pinned plugin setup and reconnect procedure](docs/authz/migration-52.md#reconnect-openclaw); actual plugin/server/browser/voice acceptance remains open. Other plugin work lives in [vauxr-openclaw/ROADMAP.md](https://github.com/vauxr-ai/vauxr-openclaw/blob/develop/ROADMAP.md).

### Security
- **Transport and trust** — HTTP/WS is the unencrypted LAN default; optional HTTPS/WSS requires strict chain/name/date verification, explicit trust provisioning and no downgrade. Follow the [proxy, microphone and renewal procedure](docs/authz/migration-52.md#httpws-and-optional-strict-tls). Positive deployed browser/firmware TLS and trustworthy device time remain acceptance work.
- **Credential lifecycle** — [lifecycle v1](docs/authz/lifecycle-v1.md) implements owner-initiated device/integration rotation with bounded overlap and durable-save ACK, terminal revoke and explicit recovery. Legacy channel export/rotation is replaced. [Rollback](docs/authz/migration-52.md#rollback) must account for revoked credentials returning in older full snapshots; automated tests do not prove physical power-loss safety.
