import type { WebSocket } from "ws";
import { loadDeviceConfigs, saveDeviceConfigs, type DeviceConfig } from "./device-config.js";
import { getConfig } from "./config.js";

export type ConnectionState = "idle" | "listening" | "processing" | "speaking" | "offline";

export interface DeviceEntry {
  id: string;
  name: string;
  ws: WebSocket;
  state: ConnectionState;
  lastSeen: Date;
  seq: number;
  abortController: AbortController | null;
  config: DeviceConfig;
}

const devices = new Map<string, DeviceEntry>();
let configs: Record<string, DeviceConfig> = {};
let configsLoaded = false;

function ensureConfigsLoaded(): void {
  if (configsLoaded) return;
  configs = loadDeviceConfigs(getConfig().dataDir);
  configsLoaded = true;
}

export function loadConfigs(): void {
  configs = loadDeviceConfigs(getConfig().dataDir);
  configsLoaded = true;
}

export function getConfigFor(deviceId: string): DeviceConfig {
  ensureConfigsLoaded();
  return configs[deviceId] ?? {};
}

export function updateConfig(deviceId: string, patch: DeviceConfig): DeviceConfig {
  ensureConfigsLoaded();
  const next: DeviceConfig = { ...(configs[deviceId] ?? {}), ...patch };
  configs[deviceId] = next;
  saveDeviceConfigs(getConfig().dataDir, configs);
  const entry = devices.get(deviceId);
  if (entry) entry.config = next;
  return next;
}

export function register(deviceId: string, ws: WebSocket, name?: string): DeviceEntry {
  abortActiveTurn(deviceId);
  ensureConfigsLoaded();
  const config = configs[deviceId] ?? {};
  const entry: DeviceEntry = {
    id: deviceId,
    name: name ?? config.name ?? deviceId,
    ws,
    state: "idle",
    lastSeen: new Date(),
    seq: 0,
    abortController: null,
    config,
  };
  devices.set(deviceId, entry);
  return entry;
}

export function unregister(deviceId: string): void {
  abortActiveTurn(deviceId);
  devices.delete(deviceId);
}

export function get(deviceId: string): DeviceEntry | undefined {
  return devices.get(deviceId);
}

export function getAll(): DeviceEntry[] {
  return Array.from(devices.values());
}

export function setState(deviceId: string, state: ConnectionState): void {
  const entry = devices.get(deviceId);
  if (entry) {
    entry.state = state;
    entry.lastSeen = new Date();
  }
}

export function abortActiveTurn(deviceId: string): void {
  const entry = devices.get(deviceId);
  if (entry?.abortController) {
    entry.abortController.abort();
    entry.abortController = null;
  }
}

export function nextSeq(deviceId: string): number {
  const entry = devices.get(deviceId);
  if (!entry) return 0;
  entry.seq = (entry.seq + 1) & 0xffff;
  return entry.seq;
}
