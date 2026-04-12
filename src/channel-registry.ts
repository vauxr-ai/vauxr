import { randomUUID, randomBytes } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import bcrypt from "bcryptjs";
import { getConfig } from "./config.js";

export interface Channel {
  id: string;
  name: string;
  type: "openclaw" | "openclaw-direct";
  tokenHash: string;
  active: boolean;
  createdAt: string;
  builtin?: boolean;
}

export type ChannelPublic = Omit<Channel, "tokenHash">;

const BCRYPT_COST = 10;
const TOKEN_PREFIX = "vx_ch_";
const TOKEN_HEX_LENGTH = 64;

let channels: Channel[] = [];
let openclawDirectActive = false;

function channelsPath(): string {
  return join(getConfig().dataDir, "channels.json");
}

function configPath(): string {
  return join(getConfig().dataDir, "config.json");
}

export function load(): void {
  const dataDir = getConfig().dataDir;
  if (!existsSync(dataDir)) {
    mkdirSync(dataDir, { recursive: true });
  }

  const p = channelsPath();
  if (existsSync(p)) {
    const raw = readFileSync(p, "utf-8");
    channels = JSON.parse(raw) as Channel[];
  } else {
    channels = [];
  }

  // Load openclaw-direct active state. On first run (no config.json yet),
  // default to active when OPENCLAW_URL is configured so users get a working
  // built-in direct channel without manual activation.
  const cp = configPath();
  if (existsSync(cp)) {
    const raw = readFileSync(cp, "utf-8");
    const cfg = JSON.parse(raw) as { openclawDirectActive?: boolean };
    openclawDirectActive = cfg.openclawDirectActive ?? false;
  } else if (getConfig().openclaw.url && channels.length === 0) {
    openclawDirectActive = true;
    saveConfig();
  } else {
    openclawDirectActive = false;
  }
}

function save(): void {
  const dataDir = getConfig().dataDir;
  if (!existsSync(dataDir)) {
    mkdirSync(dataDir, { recursive: true });
  }
  writeFileSync(channelsPath(), JSON.stringify(channels, null, 2));
}

function saveConfig(): void {
  const dataDir = getConfig().dataDir;
  if (!existsSync(dataDir)) {
    mkdirSync(dataDir, { recursive: true });
  }
  writeFileSync(configPath(), JSON.stringify({ openclawDirectActive }, null, 2));
}

function generateToken(): string {
  return TOKEN_PREFIX + randomBytes(TOKEN_HEX_LENGTH / 2).toString("hex");
}

function getOpenClawDirectChannel(): Channel | null {
  const config = getConfig();
  if (!config.openclaw.url) return null;

  return {
    id: "openclaw-direct",
    name: "OpenClaw Direct",
    type: "openclaw-direct",
    tokenHash: "",
    active: openclawDirectActive,
    createdAt: new Date(0).toISOString(),
    builtin: true,
  };
}

export function getAll(): ChannelPublic[] {
  const result: ChannelPublic[] = [];

  const direct = getOpenClawDirectChannel();
  if (direct) {
    const { tokenHash: _, ...pub } = direct;
    result.push(pub);
  }

  for (const ch of channels) {
    const { tokenHash: _, ...pub } = ch;
    result.push(pub);
  }

  return result;
}

export function getById(id: string): Channel | undefined {
  if (id === "openclaw-direct") {
    return getOpenClawDirectChannel() ?? undefined;
  }
  return channels.find((c) => c.id === id);
}

export function getActive(): Channel | undefined {
  const direct = getOpenClawDirectChannel();
  if (direct?.active) return direct;
  return channels.find((c) => c.active);
}

export async function create(name: string, type: "openclaw"): Promise<{ channel: ChannelPublic; token: string }> {
  const token = generateToken();
  const tokenHash = await bcrypt.hash(token, BCRYPT_COST);

  const channel: Channel = {
    id: randomUUID(),
    name,
    type,
    tokenHash,
    active: false,
    createdAt: new Date().toISOString(),
  };

  channels.push(channel);
  save();

  const { tokenHash: _, ...pub } = channel;
  return { channel: pub, token };
}

export function remove(id: string): boolean {
  if (id === "openclaw-direct") return false;
  const idx = channels.findIndex((c) => c.id === id);
  if (idx === -1) return false;
  channels.splice(idx, 1);
  save();
  return true;
}

export function activate(id: string): boolean {
  if (id === "openclaw-direct") {
    const direct = getOpenClawDirectChannel();
    if (!direct) return false;

    // Deactivate all stored channels
    for (const ch of channels) ch.active = false;
    save();

    openclawDirectActive = true;
    saveConfig();
    return true;
  }

  const target = channels.find((c) => c.id === id);
  if (!target) return false;

  // Deactivate all
  for (const ch of channels) ch.active = false;
  openclawDirectActive = false;
  saveConfig();

  target.active = true;
  save();
  return true;
}

export async function rotateToken(id: string): Promise<string | null> {
  if (id === "openclaw-direct") return null;
  const ch = channels.find((c) => c.id === id);
  if (!ch) return null;

  const token = generateToken();
  ch.tokenHash = await bcrypt.hash(token, BCRYPT_COST);
  save();
  return token;
}

export async function validateChannelToken(rawToken: string): Promise<Channel | null> {
  for (const ch of channels) {
    if (await bcrypt.compare(rawToken, ch.tokenHash)) {
      return ch;
    }
  }
  return null;
}
