import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

// Set env vars before any imports
process.env.DEVICE_TOKEN = "test-device-token";
process.env.WHISPER_URL = "tcp://127.0.0.1:10300";
process.env.PIPER_URL = "tcp://127.0.0.1:10200";

import * as channelRegistry from "../src/channel-registry.js";
import { resetConfig } from "../src/config.js";

let dataDir: string;

beforeEach(() => {
  dataDir = mkdtempSync(join(tmpdir(), "vauxr-test-"));
  process.env.DATA_DIR = dataDir;
  process.env.OPENCLAW_URL = "";
  process.env.OPENCLAW_TOKEN = "";
  resetConfig();
  channelRegistry.load();
});

afterEach(() => {
  rmSync(dataDir, { recursive: true, force: true });
});

describe("channel-registry", () => {
  // ── Create ──

  it("creates a channel — returns channel with token, stores hash not raw token", async () => {
    const { channel, token } = await channelRegistry.create("Test Channel", "openclaw");
    expect(channel.name).toBe("Test Channel");
    expect(channel.type).toBe("openclaw");
    expect(channel.active).toBe(false);
    expect(channel.id).toBeTruthy();
    expect(channel.createdAt).toBeTruthy();
    expect(token).toBeTruthy();
    // Public channel should not have tokenHash
    expect((channel as Record<string, unknown>).tokenHash).toBeUndefined();
  });

  it("creates a channel — token format matches vx_ch_ + 64 hex chars", async () => {
    const { token } = await channelRegistry.create("Test Channel", "openclaw");
    expect(token).toMatch(/^vx_ch_[0-9a-f]{64}$/);
  });

  // ── List ──

  it("lists channels — omits tokenHash, includes all fields", async () => {
    await channelRegistry.create("Channel A", "openclaw");
    const list = channelRegistry.getAll();
    expect(list).toHaveLength(1);
    expect(list[0]!.name).toBe("Channel A");
    expect(list[0]!.type).toBe("openclaw");
    expect(list[0]!.active).toBe(false);
    expect(list[0]!.id).toBeTruthy();
    expect(list[0]!.createdAt).toBeTruthy();
    expect((list[0] as Record<string, unknown>).tokenHash).toBeUndefined();
  });

  it("lists channels — includes virtual openclaw-direct when OPENCLAW_URL is set", async () => {
    process.env.OPENCLAW_URL = "wss://test:18789";
    resetConfig();
    const list = channelRegistry.getAll();
    const direct = list.find((c) => c.id === "openclaw-direct");
    expect(direct).toBeTruthy();
    expect(direct!.type).toBe("openclaw-direct");
    expect(direct!.builtin).toBe(true);
    expect(direct!.active).toBe(false);
  });

  it("lists channels — omits virtual openclaw-direct when OPENCLAW_URL is not set", () => {
    process.env.OPENCLAW_URL = "";
    resetConfig();
    const list = channelRegistry.getAll();
    expect(list.find((c) => c.id === "openclaw-direct")).toBeUndefined();
  });

  // ── Activate ──

  it("activates a channel — sets active=true, deactivates previously active channel", async () => {
    const { channel: chA } = await channelRegistry.create("A", "openclaw");
    const { channel: chB } = await channelRegistry.create("B", "openclaw");

    channelRegistry.activate(chA.id);
    expect(channelRegistry.getById(chA.id)!.active).toBe(true);
    expect(channelRegistry.getById(chB.id)!.active).toBe(false);

    channelRegistry.activate(chB.id);
    expect(channelRegistry.getById(chA.id)!.active).toBe(false);
    expect(channelRegistry.getById(chB.id)!.active).toBe(true);
  });

  it("activates openclaw-direct — works without it being in channels.json", () => {
    process.env.OPENCLAW_URL = "wss://test:18789";
    resetConfig();
    const ok = channelRegistry.activate("openclaw-direct");
    expect(ok).toBe(true);
    const active = channelRegistry.getActive();
    expect(active).toBeTruthy();
    expect(active!.id).toBe("openclaw-direct");
    expect(active!.type).toBe("openclaw-direct");
  });

  it("activating a channel deactivates openclaw-direct", async () => {
    process.env.OPENCLAW_URL = "wss://test:18789";
    resetConfig();
    channelRegistry.activate("openclaw-direct");
    expect(channelRegistry.getActive()!.id).toBe("openclaw-direct");

    const { channel } = await channelRegistry.create("My Channel", "openclaw");
    channelRegistry.activate(channel.id);
    expect(channelRegistry.getActive()!.id).toBe(channel.id);
    // openclaw-direct should be inactive
    const direct = channelRegistry.getById("openclaw-direct");
    expect(direct!.active).toBe(false);
  });

  // ── Delete ──

  it("deletes a channel — removes from list", async () => {
    const { channel } = await channelRegistry.create("Doomed", "openclaw");
    expect(channelRegistry.getAll()).toHaveLength(1);
    const ok = channelRegistry.remove(channel.id);
    expect(ok).toBe(true);
    expect(channelRegistry.getAll()).toHaveLength(0);
  });

  it("deletes a non-existent channel — returns false", () => {
    const ok = channelRegistry.remove("nonexistent-id");
    expect(ok).toBe(false);
  });

  it("deletes openclaw-direct (builtin) — returns false, cannot delete", () => {
    process.env.OPENCLAW_URL = "wss://test:18789";
    resetConfig();
    const ok = channelRegistry.remove("openclaw-direct");
    expect(ok).toBe(false);
    // It should still be in the list
    const direct = channelRegistry.getById("openclaw-direct");
    expect(direct).toBeTruthy();
  });

  // ── Rotate ──

  it("rotates token — new token validates, old token no longer validates", async () => {
    const { channel, token: oldToken } = await channelRegistry.create("Rotate Me", "openclaw");

    // Old token should validate
    const validOld = await channelRegistry.validateChannelToken(oldToken);
    expect(validOld).toBeTruthy();
    expect(validOld!.id).toBe(channel.id);

    // Rotate
    const newToken = await channelRegistry.rotateToken(channel.id);
    expect(newToken).toBeTruthy();
    expect(newToken).toMatch(/^vx_ch_[0-9a-f]{64}$/);
    expect(newToken).not.toBe(oldToken);

    // New token validates
    const validNew = await channelRegistry.validateChannelToken(newToken!);
    expect(validNew).toBeTruthy();
    expect(validNew!.id).toBe(channel.id);

    // Old token no longer validates
    const invalidOld = await channelRegistry.validateChannelToken(oldToken);
    expect(invalidOld).toBeNull();
  });

  it("rotates openclaw-direct (builtin) — returns null, cannot rotate", async () => {
    process.env.OPENCLAW_URL = "wss://test:18789";
    resetConfig();
    const result = await channelRegistry.rotateToken("openclaw-direct");
    expect(result).toBeNull();
  });

  // ── Persistence ──

  it("load/save roundtrip — persists to channels.json and reloads correctly", async () => {
    const { channel, token } = await channelRegistry.create("Persistent", "openclaw");
    channelRegistry.activate(channel.id);

    // Reload
    channelRegistry.load();
    const list = channelRegistry.getAll();
    expect(list).toHaveLength(1);
    expect(list[0]!.name).toBe("Persistent");
    expect(list[0]!.active).toBe(true);

    // Token still validates after reload
    const valid = await channelRegistry.validateChannelToken(token);
    expect(valid).toBeTruthy();
    expect(valid!.id).toBe(channel.id);
  });
});
