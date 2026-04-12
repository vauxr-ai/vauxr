import type { WebSocket } from "ws";

export type ConnectionState = "idle" | "listening" | "processing" | "speaking" | "offline";

export interface DeviceEntry {
  id: string;
  name: string;
  ws: WebSocket;
  state: ConnectionState;
  lastSeen: Date;
  seq: number;
  abortController: AbortController | null;
}

const devices = new Map<string, DeviceEntry>();

export function register(deviceId: string, ws: WebSocket, name?: string): DeviceEntry {
  abortActiveTurn(deviceId);
  const entry: DeviceEntry = {
    id: deviceId,
    name: name ?? deviceId,
    ws,
    state: "idle",
    lastSeen: new Date(),
    seq: 0,
    abortController: null,
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
