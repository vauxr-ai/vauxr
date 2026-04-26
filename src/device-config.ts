import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";

export type FollowUpMode = "auto" | "always" | "never";

export interface DeviceConfig {
  name?: string;
  voice?: boolean;
  follow_up_mode?: FollowUpMode;
  output_sample_rate?: number;
}

const VALID_FOLLOW_UP_MODES: ReadonlySet<FollowUpMode> = new Set(["auto", "always", "never"]);
const KNOWN_FIELDS: ReadonlySet<keyof DeviceConfig> = new Set(["name", "voice", "follow_up_mode", "output_sample_rate"]);

export function deviceConfigPath(dataDir: string): string {
  return join(dataDir, "devices.json");
}

function sanitizeEntry(deviceId: string, raw: unknown): DeviceConfig {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const cfg: DeviceConfig = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!KNOWN_FIELDS.has(key as keyof DeviceConfig)) continue;
    if (key === "name" && typeof value === "string") {
      cfg.name = value;
    } else if (key === "voice" && typeof value === "boolean") {
      cfg.voice = value;
    } else if (key === "follow_up_mode") {
      if (typeof value === "string" && VALID_FOLLOW_UP_MODES.has(value as FollowUpMode)) {
        cfg.follow_up_mode = value as FollowUpMode;
      } else {
        console.warn(`[device-config] Invalid follow_up_mode for ${deviceId}: ${String(value)} — treating as "auto"`);
        cfg.follow_up_mode = "auto";
      }
    } else if (key === "output_sample_rate") {
      if (typeof value === "number" && value > 0) {
        cfg.output_sample_rate = value;
      }
    }
  }
  return cfg;
}

export function loadDeviceConfigs(dataDir: string): Record<string, DeviceConfig> {
  const path = deviceConfigPath(dataDir);
  if (!existsSync(path)) return {};

  let parsed: unknown;
  try {
    const raw = readFileSync(path, "utf-8");
    parsed = JSON.parse(raw);
  } catch (err) {
    console.warn(`[device-config] Failed to parse ${path}: ${(err as Error).message}`);
    return {};
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    console.warn(`[device-config] ${path} is not a JSON object — ignoring`);
    return {};
  }

  const result: Record<string, DeviceConfig> = {};
  for (const [deviceId, entry] of Object.entries(parsed as Record<string, unknown>)) {
    result[deviceId] = sanitizeEntry(deviceId, entry);
  }
  return result;
}

export function saveDeviceConfigs(dataDir: string, configs: Record<string, DeviceConfig>): void {
  if (!existsSync(dataDir)) {
    mkdirSync(dataDir, { recursive: true });
  }
  writeFileSync(deviceConfigPath(dataDir), JSON.stringify(configs, null, 2));
}
