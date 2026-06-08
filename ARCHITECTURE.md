# Vauxr — Architecture

Vauxr is a self-hosted voice gateway that gives any voice device a full STT → LLM → TTS pipeline. It connects hardware to OpenClaw (or any LLM backend) with no port forwarding, no sidecar services, and no cloud dependency required.

---

## The Big Picture

```
┌─────────────────────────────────────────────────────┐
│                   Voice Device                      │
│  (mic + speaker)                                    │
│                                                     │
│  vauxr_client (device library)                   │
│  - mic capture + VAD                                │
│  - Vauxr WS protocol                             │
│  - audio playback                                   │
└───────────────────┬─────────────────────────────────┘
                    │ WebSocket (Vauxr WS protocol)
                    ▼
┌─────────────────────────────────────────────────────┐
│                Vauxr                             │
│  (self-hosted Docker stack)                      │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌─────────────────┐   │
│  │ Whisper  │  │  Piper   │  │     vauxr        │   │
│  │ (STT)    │  │  (TTS)   │  │   (WS bridge)    │   │
│  │ Wyoming  │  │ Wyoming  │  │                  │   │
│  └──────────┘  └──────────┘  └────────┬────────┘   │
│                                        │            │
└────────────────────────────────────────┼────────────┘
                                         │ OpenClaw native WS
                                         ▼ (or raw LLM API)
                              ┌─────────────────────┐
                              │      OpenClaw        │
                              │  (local or cloud)    │
                              │  - persona + memory  │
                              │  - tools + cron      │
                              │  - proactive sends   │
                              └─────────────────────┘
```

---

## Repos

| Repo | Description |
|---|---|
| `vauxr` | Docker stack + WS bridge server (this repo) |
| `vauxr-openclaw` | OpenClaw channel plugin (deep integration + relay) |

---

## Components

### 1. vauxr (voice gateway server)

The core of the stack. A Node.js WebSocket server that:

- Accepts device connections (Vauxr WS protocol)
- Receives audio chunks from the device
- Forwards audio to Whisper (Wyoming) for transcription
- Sends transcript to OpenClaw via native WS protocol (`chat.send`)
- Subscribes to `chat` events for streaming reply deltas
- Streams TTS audio back to the device via Piper, flushing buffered
  text early whenever the agent's delta stream goes idle long enough
  to indicate a real pause (e.g. tool call or reasoning) so playback
  starts before the full reply is generated. Idle threshold is
  configurable via `STREAMING_TTS_IDLE_PAUSE_MS` (default `400`).

### 2. wyoming-faster-whisper (STT)

Local STT — no cloud, no API key. Runs via Wyoming protocol.

### 3. wyoming-piper (TTS)

Local TTS — no cloud, no API key. Runs via Wyoming protocol.

> **Note:** When connecting to OpenClaw, `talk.speak` (OpenClaw's built-in TTS via ElevenLabs/OpenAI/Microsoft) can optionally replace Piper for higher quality voice. Piper is the default for zero-config deployments.

---

## Vauxr WS Protocol

The protocol uses two frame types over a single WebSocket connection:

- **JSON text frames** — control messages (signalling, metadata)
- **Binary frames** — raw audio (zero encoding overhead)

Frames are distinguished by type: if the first byte is `0x7B` (`{`), it's JSON. Otherwise it's a binary audio frame.

### Control messages (JSON text frames)

**Device → Server:**
```jsonc
// Boot-time handshake: advertise capabilities. The device is dumb — the server
// decides the realtime policy (see "Realtime (WebRTC) Hybrid" below).
{ "type": "hello", "device_id": "...", "token": "...", "platform": "satellite1", "caps": ["ws", "webrtc"] }

// Wake word detected, starting voice turn
{ "type": "voice.start", "device_id": "...", "token": "..." }

// VAD detected end of speech
{ "type": "voice.end" }

// User interrupted — abort current response
{ "type": "abort" }
```

**Server → Device:**
```jsonc
// Handshake reply: realtime policy. transport is "ws" (default) or "webrtc".
// offer_url + stun are only present when transport is "webrtc".
{ "type": "hello", "realtime": { "enabled": true, "transport": "webrtc",
                                 "offer_url": "http://<host>:8080/api/offer",
                                 "stun": "stun:stun.l.google.com:19302" } }

// Auth OK, ready for voice
{ "type": "ready" }

// STT result (useful for device display)
{ "type": "transcript", "text": "what's the weather today?" }

// All TTS audio sent for this turn. In realtime mode this carries follow_up:
// true keeps the device listening, false ends the realtime session.
{ "type": "audio.end", "follow_up": false }

// Control command (from HTTP API or agent tool)
{ "type": "device.control", "command": "set_volume" | "mute" | "unmute" | "reboot", "params": { ... } }

// Error
{ "type": "error", "code": "...", "message": "..." }
```

### Audio frames (binary)

All audio is sent as raw binary WebSocket frames with a 3-byte header:

```
[1 byte: message type][2 bytes: sequence number, big-endian][remaining bytes: raw audio]
```

**Message type byte:**

| Value | Direction | Content |
|---|---|---|
| `0x01` | Device → Server | Mic audio — raw PCM 16-bit, 16kHz, mono |
| `0x02` | Server → Device | TTS audio — raw PCM (from Piper/Wyoming) |
| `0x03` | Server → Device | Proactive push audio — raw PCM |

The sequence number allows the device to detect dropped or reordered frames and discard stale audio.

**Why binary frames?**
Base64 encoding audio inside JSON adds ~33% overhead with no benefit. Since we control both sides of the connection, we can use the most efficient format available. Binary WebSocket frames are natively supported by all major implementations.

---

## Realtime (WebRTC) Hybrid

For low-latency, barge-in conversation, capable devices (currently Satellite1, which has hardware XMOS AEC) can upgrade the media path to **WebRTC** while keeping the Vauxr WS connection for control. The server runs an in-process [Pipecat](https://github.com/pipecat-ai/pipecat) `SmallWebRTC` pipeline (`STT → channel-LLM → TTS`) gated behind `REALTIME_ENABLED=1`.

The device stays dumb: it advertises capabilities in `hello` and the server returns the policy. Whether realtime is used, which transport, and the WebRTC endpoints are all decided server-side.

### Lifecycle

```
boot:     device --hello{caps:[ws,webrtc]}--> server
          device <--hello{realtime:{transport:webrtc,offer_url,stun}}-- server

wake:     device --realtime.start{device_id,token}--> server   (server arms pre-roll)
          device <--ready-- server
          device --0x01 mic frames (pre-roll: the wake-word command)--> server
                  (buffered server-side, seeded into the pipeline head)

connect:  device --HTTP POST /api/offer {sdp,type,device_id,token}--> server
          device <--{sdp,type:answer}-- server      (per-device auth on /api/offer)
          ICE/DTLS-SRTP established; bidirectional Opus audio over WebRTC

ready:    device --realtime.media_ready--> server    (mic now on WebRTC track;
                                                       server stops forwarding WS pre-roll)

turn(s):  device <--transcript{text}-- server
          device <--audio.start-- server   (bot speaking; over WebRTC media)
          device <--audio.end{follow_up}-- server
                  follow_up:true  -> stay listening (another turn)
                  follow_up:false -> leave realtime, back to silent wake-word wait

stop:     device --realtime.stop--> server   (or WS disconnect tears down the session)
```

### Pre-roll

WebRTC takes ~1–2s to negotiate (ICE + DTLS), so the wake-word command itself would be lost. On `realtime.start` the server arms a pre-roll buffer; the device keeps sending mic audio as WS `0x01` binary frames until `realtime.media_ready`. The buffered pre-roll is seeded into the pipeline head so the first utterance isn't dropped, then media switches to the WebRTC track.

### follow_up exit

The realtime session reuses the same `follow_up` mechanism as the turn-based WS pipeline (`follow_up_mode`: `auto` | `always` | `never`, and the `[[follow_up]]` reply tag). Each turn ends with `audio.end{follow_up}`; on `follow_up:false` both sides tear down — the device returns to silent wake-word waiting and the server stops the Pipecat session.

### Config

```env
REALTIME_ENABLED=1
REALTIME_HOST=<device-reachable-lan-ip>   # used to build offer_url + for SDP munging (esp32 mode)
REALTIME_STUN_URL=stun:stun.l.google.com:19302
```

> WebRTC (aiortc) uses ephemeral UDP ports for ICE, so the realtime build runs the stack with `network_mode: host`. See `docker-compose.yml`.

---

## OpenClaw Integration

vauxr connects to OpenClaw using OpenClaw's **native gateway WebSocket protocol** — the same protocol used by the CLI and companion apps. No OpenClaw plugin required for basic operation.

vauxr supports two routing modes via a channel registry:

- **openclaw-direct** — vauxr connects outbound to OpenClaw WS (`chat.send` / `chat` events), collects the full reply, then synthesizes via Piper and streams to device
- **channel plugin** — the `vauxr-openclaw` plugin connects inbound to vauxr's `/channel` WS path, handles LLM routing, and streams response deltas back; vauxr synthesizes via Piper and sends to device

Persistent per-device session key: `vauxr:${device_id}`

**Config:**
```env
OPENCLAW_URL=wss://your-openclaw.example.com:18789
OPENCLAW_TOKEN=your-gateway-token
```

---

## Deployment

### Self-hosted (local)

Copy `.env.example` to `.env`, fill in your values, then:

```bash
docker compose up -d
```

Device connects to `ws://your-server-ip:8765`.

---

## OpenClaw Channel Plugin (`vauxr-openclaw`)

An optional OpenClaw plugin for deeper integration. Instead of vauxr connecting outbound to OpenClaw, the plugin connects inbound to vauxr's `/channel` WS endpoint — no OpenClaw credentials needed on the vauxr side.

**What it adds:**
- Voice sessions appear in OpenClaw's session list
- Proactive replies (cron, tools, agent actions) automatically route to the speaker
- Device shows up in OpenClaw `/status`

---

## `vauxr_client` Device Library

A drop-in client library for connecting voice hardware to Vauxr. The reference implementation is an ESP-IDF component, but the Vauxr WS protocol is simple enough to implement on any platform that supports WebSocket.

**Scope (audio only):**
- Mic capture
- Energy VAD
- Vauxr WS protocol client
- Audio playback

**Explicitly out of scope** (host application's responsibility):
- Network / WiFi management
- LED states / animations
- Wake word detection
- Display / UI

ESP-IDF component will be published to the ESP-IDF Component Registry. Ports to other platforms welcome.

---

## Security

- Device auth: per-device bearer token issued at pairing
- Pairing: approval via OpenClaw `/pair` command or Vauxr UI
- Transport: TLS (`wss://`) for all production deployments
- Tokens: scoped per device, revocable


