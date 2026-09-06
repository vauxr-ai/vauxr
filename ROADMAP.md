# Vauxr Roadmap

---

## Planned

Features grouped by theme. No ordering assigned.

### Conversation Quality
- ~~**Follow-up mode** — server sends `follow_up` flag; device stays in listening state automatically after a response~~ ✅
- ~~**Interruption** — server `abort` + per-device `barge_in` config. Firmware still needs Satellite1 hardware validation (see vauxr-assistant ROADMAP).~~ ✅
- **Response sanitization for voice** — strip emojis, code blocks, markdown formatting, URLs, and other non-spoken content from LLM output before TTS; catches what voice formatting prompts miss so the device never reads aloud ` ```python ` or 🔥
- ~~**Streaming TTS via idle-pause detection** — flush buffered assistant text to Piper whenever the delta stream goes idle (default 400ms) so the device starts speaking while the agent is still thinking or running tools, instead of waiting for the full reply~~ ✅

### Device Management
- **`OPERATOR_TOKEN` for admin auth** — rename `DEVICE_TOKEN` to `OPERATOR_TOKEN` and scope it to admin operations only (channel CRUD, device provisioning, web UI access). Removes the device-side meaning entirely. Solves the bootstrap chicken-and-egg: per-device tokens come from the device-management API, and that API is itself gated by `OPERATOR_TOKEN`. Channel tokens (`vx_ch_…`) remain the auth for HTTP API consumers like the OpenClaw plugin.
- **Device management REST API remainder** — `GET`/`PATCH /api/devices` already list and rename. Still open: create / revoke, and a unique token per device at registration. Replaces the shared `DEVICE_TOKEN` `.env` bootstrap. Gated by `OPERATOR_TOKEN`.
- **Device management web UI remainder** — DevicesPanel already lists, names, follow-up, barge-in, buttons, commands, and last-seen. Still open: add/revoke devices and per-device token management.

### Home Assistant Integration
- **Vauxr STT/TTS providers for HA** — HA sees stable "Vauxr STT" and "Vauxr TTS" entities that speak the Vauxr WS protocol under the hood. HA users can route their voice pipeline through Vauxr without ever exposing Whisper/Piper TCP ports directly. Distinct from the firmware ROADMAP's HA event forwarder (`vauxr.wake` etc. to `/api/events`) and from the existing webhook dispatcher.

### Provider Abstraction
- **STTProvider / TTSProvider extension system** — pluggable provider interface so Whisper and Piper become one option among many. Swap in Deepgram, ElevenLabs, Groq Whisper, Coqui, or any other STT/TTS backend without touching device firmware or the WS protocol. Keeps the device-facing protocol stable while the backend evolves.

### Device Context & Voice Formatting
- ~~**Server-side device registry** (`devices.json` keyed by `device_id`, fields: `name`, `voice`, plus follow-up, barge-in, button actions)~~ ✅
- ~~**Session preamble injection** — on first turn of each session, server prepends hidden context to `chat.send` with device name and voice formatting rules (no emojis, no markdown, concise spoken sentences)~~ ✅ *(via `vauxr-openclaw` channel plugin's `voiceSystemPrompt`)*

### Transcription Accuracy
- **Conversation context for Whisper** — pass recent conversation history as an initial prompt to the Whisper API (`initial_prompt` field); primes the model with relevant vocabulary, proper nouns, and topic context from the current session, improving accuracy especially for domain-specific terms and follow-up questions

### Audio Quality
- **Sibilance / hiss on "s" sounds** — TTS output has white noise on sibilants (sounds like "sh"); needs investigation: Piper voice model selection, sample rate / bit depth in the audio pipeline, MP3 encoding settings

### Multi-Device & Proximity Detection
- **Wake word dedup** — when multiple devices hear the wake word simultaneously, server arbitrates: devices include a confidence score with the wake event, server holds a ~500ms dedup window, highest-confidence device wins (closest device naturally tends to win), losers receive a `cancel` frame to abort listening; prevents duplicate STT submissions and overlapping spoken responses

### Server-Initiated Control
- **Audio stream playback** — server sends a `device.play` control frame over WS containing a URL; device connects to the URL and streams + plays the audio as it downloads; enables music playback, internet radio, audio clips, or any audio source reachable by the device
- **Stop playback** — server sends a `device.stop` control frame to interrupt any currently playing audio (TTS or stream)
- ~~**Push TTS / announce** — `POST /api/devices/{id}/announce` synthesizes text via Piper and streams as `0x03` push audio frames to device; enables cron jobs, heartbeats, and proactive agent alerts to speak through the device~~ ✅
- ~~**Device control from OpenClaw** — `POST /api/devices/{id}/command` sends a `device.control` JSON frame (e.g. `set_volume`, `mute`, `reboot`); enables voice commands like "set the volume to 10" to actually change device state~~ ✅
- ~~**OTA firmware updates** — `device.control` `ota` with `params.url`; images served from `DATA_DIR/firmware/<platform>.bin`. Device-side dual-slot + rollback lives in vauxr-assistant.~~ ✅
- **Device queries / telemetry** — bidirectional: server can request data from the device and await a response (e.g. "what's your battery level?"); device responds with a `device.response` frame; server surfaces the answer back to OpenClaw

### OpenClaw Channel Plugin (`vauxr-openclaw`)
- ~~Optional plugin for deeper OpenClaw integration~~ ✅
- ~~**Relay mode**: plugin opens outbound WS from local OpenClaw to Vauxr — no port forwarding needed~~ ✅

Plugin-only remaining work lives in [vauxr-openclaw/ROADMAP.md](https://github.com/vauxr-ai/vauxr-openclaw/blob/develop/ROADMAP.md). Pairing / `/pair` / per-device `/status` are out of scope (the plugin opts out).

### Security
- **WSS / TLS transport** — currently using plain `ws://`; production deployments should use `wss://`; needs TLS cert handling on the server side and `esp_tls` on the ESP32 (ESP-IDF has built-in support)
- **Certificate validation** — device should verify server cert; for self-hosted setups, support custom CA bundle baked into firmware
- **Token rotation** — per-device bearer tokens should be rotatable without re-pairing (channel-token rotate already ships)
