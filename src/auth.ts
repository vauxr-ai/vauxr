import { getConfig } from "./config.js";
import { timingSafeEqual } from "node:crypto";
import { validateChannelToken } from "./channel-registry.js";

export interface AuthResult {
  ok: boolean;
  reason?: string;
}

/** Validates a device token (used by ESP32 devices over WebSocket). */
export function validateToken(token: string): AuthResult {
  const expected = getConfig().device.token;

  if (token.length !== expected.length) {
    return { ok: false, reason: "Invalid token" };
  }

  const a = Buffer.from(token, "utf-8");
  const b = Buffer.from(expected, "utf-8");

  if (!timingSafeEqual(a, b)) {
    return { ok: false, reason: "Invalid token" };
  }

  return { ok: true };
}

/** Validates a channel token (used by OpenClaw over HTTP API). */
export async function validateChannelHttpToken(token: string): Promise<AuthResult> {
  const channel = await validateChannelToken(token);
  if (!channel) {
    return { ok: false, reason: "Invalid token" };
  }
  return { ok: true };
}
