# Pipecat Realtime PoC

Isolated spike to **feel** WebRTC + Pipecat (barge-in, streaming, turn-taking) before committing to hybrid WS/WebRTC transport switching in vauxr or firmware.

**Not in scope:** `transport.switch`, vauxr_client changes, production embedding.

---

## What you're comparing

| | **This PoC** | **Production vauxr (WS)** |
|---|---|---|
| Transport | WebRTC (SmallWebRTC) | WebSocket binary PCM |
| Turn model | Continuous + Silero VAD; interrupt anytime | Wake → speak → VAD end → response |
| Barge-in | Native (talk over the bot) | `abort` + restart (partially wired) |
| Stack | Same Whisper/Piper if `POC_BACKEND=wyoming` | Same docker compose |

Run both side-by-side: PoC on `:7860`, vauxr on `:8765/ws`.

---

## Prerequisites

1. **Whisper + Piper** — start the existing stack (PoC only needs whisper/piper, not the vauxr server):

   ```bash
   cd /path/to/vauxr
   docker compose up -d whisper piper
   ```

2. **LLM** — one of:
   - `OPENAI_API_KEY` in `.env` (fastest to try)
   - `LLM_BASE_URL` + `LLM_API_KEY` pointing at OpenClaw's `/v1` HTTP API

---

## Run

```bash
cd poc/pipecat-realtime
cp .env.example .env
# edit .env — add OPENAI_API_KEY (or OpenClaw URL)

python3 -m venv .venv
source .venv/bin/activate
pip install .

# LAN-accessible (for phone browser or ESP32)
python bot.py -t webrtc --host 0.0.0.0
```

Open **http://\<your-ip\>:7860/client** — allow mic, talk, try interrupting mid-sentence.

---

## ESP32 (optional)

Uses [pipecat-esp32](https://github.com/pipecat-ai/pipecat-esp32) separately — no vauxr-assistant changes.

```bash
# Server (ESP32 SDP munging)
python bot.py -t webrtc --esp32 --host 192.168.1.10

# Device env
export PIPECAT_SMALLWEBRTC_URL=http://192.168.1.10:7860/api/offer
```

Compare feel vs your Waveshare on normal vauxr WS.

---

## What to notice

Use this checklist when deciding if hybrid transport is worth it:

- **Time to first audio** after you stop speaking
- **Barge-in** — can you cut off a long answer naturally?
- **Turn-taking** — does always-on mic feel right for a kitchen speaker, or too "phone call"?
- **False triggers** — background noise starting turns?
- **Follow-up rhythm** — is rapid back-and-forth better here than WS + `follow_up`?

If WS already feels good except barge-in, that supports **upgrade-to-WebRTC-only-during-playback** later. If continuous conversation feels better throughout, that's a different embed strategy.

---

## Config

| Variable | Default | Notes |
|---|---|---|
| `POC_BACKEND` | `wyoming` | `wyoming` = docker whisper/piper; `piper_embedded` = local Piper model |
| `WHISPER_URL` | `tcp://127.0.0.1:10300` | Wyoming STT |
| `PIPER_URL` | `tcp://127.0.0.1:10200` | Wyoming TTS |
| `PIPER_VOICE` | `en_US-libritts_r-medium` | Same as vauxr default |
| `OPENAI_API_KEY` | — | Or use `LLM_*` for OpenClaw |

---

## Files

```
poc/pipecat-realtime/
├── bot.py              # Pipecat pipeline + dev runner entry
├── wyoming_services.py # STT/TTS → existing whisper/piper containers
├── pyproject.toml
├── .env.example
└── README.md
```

Delete the whole directory when you're done exploring — nothing else depends on it.
