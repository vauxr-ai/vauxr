# Vauxr Roadmap

---

## Planned

Features grouped by theme. No ordering assigned.

### Conversation Quality
- **Follow-up mode** — server sends `follow_up` flag; device stays in listening state automatically after a response
- **Interruption** — wake word fires during playback to abort and start a new turn; full-duplex + AEC via ESP-SR AFE (ESP32-S3 supports simultaneous I2S TX/RX, AEC built into the same AFE framework as VAD)
- ~~**Streaming TTS via idle-pause detection** — flush buffered assistant text to Piper whenever the delta stream goes idle (default 400ms) so the device starts speaking while the agent is still thinking or running tools, instead of waiting for the full reply~~ ✅

### Device Management
- **Device management REST API** — `/api/devices` endpoints for creating, listing, renaming, and revoking devices; each device gets its own unique token at registration. Replaces the single shared `DEVICE_TOKEN` `.env` bootstrap with proper per-device identity, auditability, and rotation.
- **Device management web UI** — section in the existing web-client for adding, naming, and revoking devices via the browser; shows connection state, last-seen time, and per-device token management. Pairs with the REST API above and replaces the earlier "device pairing web UI" idea with a full device lifecycle surface.

### Home Assistant Integration
- **Vauxr STT/TTS providers for HA** — HA sees stable "Vauxr STT" and "Vauxr TTS" entities that speak the Vauxr WS protocol under the hood. HA users can route their voice pipeline through Vauxr without ever exposing Whisper/Piper TCP ports directly. Whisper/Piper TCP endpoints are internal to the Vauxr stack — HA integration goes through the Vauxr protocol, not around it. Lets HA users benefit from Vauxr's backend flexibility while keeping HA's conversation agent and intent system for automations.

### Provider Abstraction
- **STTProvider / TTSProvider extension system** — pluggable provider interface so Whisper and Piper become one option among many. Swap in Deepgram, ElevenLabs, Groq Whisper, Coqui, or any other STT/TTS backend without touching device firmware or the WS protocol. Keeps the device-facing protocol stable while the backend evolves.

### Device Context & Voice Formatting
- **Server-side device registry** (`devices.json` keyed by `device_id`, fields: `name`, `voice: bool`)
- ~~**Session preamble injection** — on first turn of each session, server prepends hidden context to `chat.send` with device name and voice formatting rules (no emojis, no markdown, concise spoken sentences)~~ ✅ *(via `vauxr-openclaw` channel plugin's `voiceSystemPrompt`)*

### Transcription Accuracy
- **Conversation context for Whisper** — pass recent conversation history as an initial prompt to the Whisper API (`initial_prompt` field); primes the model with relevant vocabulary, proper nouns, and topic context from the current session, improving accuracy especially for domain-specific terms and follow-up questions

### Audio Quality
- **Sibilance / hiss on "s" sounds** — TTS output has white noise on sibilants (sounds like "sh"); needs investigation: Piper voice model selection, sample rate / bit depth in the audio pipeline, MP3 encoding settings

### Multi-Device & Proximity Detection
- **Wake word dedup** — when multiple devices hear the wake word simultaneously, server arbitrates: devices include a confidence score with the wake event, server holds a ~500ms dedup window, highest-confidence device wins (closest device naturally tends to win), losers receive a `cancel` frame to abort listening; prevents duplicate STT submissions and overlapping spoken responses

### Server-Initiated Control *(architecture TBD — likely shared HTTP server)*
- **Audio stream playback** — server sends a `device.play` control frame over WS containing a URL; device connects to the URL and streams + plays the audio as it downloads; enables music playback, internet radio, audio clips, or any audio source reachable by the device
- **Stop playback** — server sends a `device.stop` control frame to interrupt any currently playing audio (TTS or stream)
- ~~**Push TTS / announce** — `POST /api/devices/{id}/announce` synthesizes text via Piper and streams as `0x03` push audio frames to device; enables cron jobs, heartbeats, and proactive agent alerts to speak through the device~~ ✅
- ~~**Device control from OpenClaw** — `POST /api/devices/{id}/command` sends a `device.control` JSON frame (e.g. `set_volume`, `mute`, `reboot`); enables voice commands like "set the volume to 10" to actually change device state~~ ✅
- **Device queries / telemetry** — bidirectional: server can request data from the device and await a response (e.g. "what's your battery level?"); device responds with a `device.response` frame; server surfaces the answer back to OpenClaw

### OpenClaw Channel Plugin (`vauxr-openclaw`)
- ~~Optional plugin for deeper OpenClaw integration~~ ✅
- ~~**Relay mode**: plugin opens outbound WS from local OpenClaw to Vauxr — no port forwarding needed~~ ✅
- Device appears in OpenClaw `/status`, `/pair` command support

### Security
- **WSS (TLS) transport** — currently using plain `ws://`; production deployments should use `wss://`; needs TLS cert handling on the server side and `esp_tls` on the ESP32 (ESP-IDF has built-in support)
- **Certificate validation** — device should verify server cert; for self-hosted setups, support custom CA bundle baked into firmware
- **Token rotation** — per-device bearer tokens should be rotatable without re-pairing

