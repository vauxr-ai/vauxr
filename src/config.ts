function required(name: string): string {
  const val = process.env[name];
  if (!val) throw new Error(`Missing required env var: ${name}`);
  return val;
}

function optional(name: string, fallback: string): string {
  return process.env[name] || fallback;
}

function parseWyomingUrl(raw: string): { host: string; port: number } {
  const stripped = raw.replace(/^tcp:\/\//, "");
  const [host, portStr] = stripped.split(":");
  return { host: host!, port: parseInt(portStr!, 10) };
}

export function loadConfig() {
  return {
    openclaw: {
      url: optional("OPENCLAW_URL", ""),
      token: optional("OPENCLAW_TOKEN", ""),
    },
    channel: {
      wsPath: "/channel",
    },
    device: {
      token: required("DEVICE_TOKEN"),
    },
    dataDir: optional("DATA_DIR", "/data"),
    whisper: parseWyomingUrl(optional("WHISPER_URL", "tcp://whisper:10300")),
    piper: {
      ...parseWyomingUrl(optional("PIPER_URL", "tcp://piper:10200")),
      voice: optional("PIPER_VOICE", "en_US-libritts_r-medium"),
    },
    ws: {
      port: parseInt(optional("WS_PORT", "8765"), 10),
    },
    http: {
      port: parseInt(optional("HTTP_PORT", "8080"), 10),
    },
    streamingTts: {
      idlePauseMs: parseInt(optional("STREAMING_TTS_IDLE_PAUSE_MS", "400"), 10),
    },
    logLevel: optional("LOG_LEVEL", "info"),
  };
}

export type Config = ReturnType<typeof loadConfig>;

let _config: Config | null = null;

export function getConfig(): Config {
  if (!_config) _config = loadConfig();
  return _config;
}

export function resetConfig(): void {
  _config = null;
}
