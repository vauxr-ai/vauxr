import { test as base, expect, type Page } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, openSync, closeSync, writeFileSync, fsyncSync, renameSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { createInterface } from "node:readline";
import { randomBytes, generateKeyPairSync, sign } from "node:crypto";

const root = resolve(import.meta.dirname, "..");
export const id = () => randomBytes(16).toString("hex");
export interface Server {
  origin: string;
  data: string;
  claim: () => string;
  restart: () => Promise<void>;
}
async function stop(child: ChildProcess) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise<void>(resolve => {
    const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
    child.kill("SIGTERM");
  });
}
export const test = base.extend<{ server: Server }>({
  server: async ({}, use) => {
    const data = mkdtempSync(resolve(tmpdir(), "vauxr-auth53-"));
    // Allowlist environment: never inherit live routing, provider, TLS or auth settings.
    const env = { PATH: process.env.PATH, PYTHONPATH: resolve(root, "src"),
      DATA_DIR: data, REALTIME_ENABLED: "0", STT_URL: "tcp://127.0.0.1:1", TTS_URL: "tcp://127.0.0.1:1" };
    let child: ChildProcess | undefined;
    const fixture: Server = { origin: "", data,
      claim: () => execFileSync("python3", ["-c",
        "import os; from pathlib import Path; from auth_store import CredentialStore; from owner_auth import OwnerAuth; print(OwnerAuth(CredentialStore(Path(os.environ['DATA_DIR'])/'authz.json')).console_claim())"],
        { cwd: root, env: { ...env, OWNER_HTTP_ORIGIN: fixture.origin }, encoding: "utf8" }).trim(),
      restart: async () => { if (child) await stop(child); await start(); },
    };
    async function start() {
      child = spawn("python3", ["e2e/disposable_server.py"], { cwd: root,
        env: { ...env, VAUXR_TEST_PORT: fixture.origin ? new URL(fixture.origin).port : "0" }, stdio: ["ignore", "pipe", "ignore"] });
      fixture.origin = await new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Disposable server startup timed out")), 10000);
        const lines = createInterface({ input: child!.stdout! });
        child!.once("exit", () => { clearTimeout(timer); reject(new Error("Disposable server exited")); });
        lines.once("line", line => {
          clearTimeout(timer); lines.close();
          if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(line)) reject(new Error("Invalid fixture origin"));
          else resolve(line);
        });
      });
      expect((await fetch(fixture.origin + "/api/auth/status")).status).toBe(200);
    }
    try {
      await start();
      await use(fixture);
    } finally {
      if (child) await stop(child);
      rmSync(data, { recursive: true, force: true });
    }
  },
});
export { expect };

export async function login(page: Page, server: Server) {
  await page.goto(server.origin);
  await expect(page.getByRole("main")).toHaveCount(0);
  const field = page.getByLabel("Operator token or console code");
  await field.fill(server.claim());
  await page.getByText("Submit console setup/recovery code").click();
  await expect(page.locator("output")).toBeVisible();
  const token = (await page.locator("output").textContent())!;
  await page.getByText("I saved this in my password manager").click();
  await field.fill(token);
  await page.getByText("Log in with saved operator token").click();
  await expect(page.getByRole("main")).toBeVisible();
  return token;
}

export async function native(server: Server, path: string, body?: object, token?: string) {
  return fetch(server.origin + path, { method: body ? "POST" : "GET", redirect: "error",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}
export async function client(server: Server, action: string, body: object) {
  const response = await native(server, `/api/integrations/v1/${action}`, body);
  expect(response.status).toBe(200);
  return response.json();
}
export async function requestIntegration(server: Server, display_name = "Synthetic OpenClaw") {
  const secret = { request_id: id(), request_secret: randomBytes(32).toString("hex") };
  const request = await client(server, "request", { ...secret, origin: server.origin, display_name,
    expires_at: Math.floor(Date.now() / 1000) + 290 });
  return { secret, request };
}

// Synthetic native client storage, not the plugin SDK or firmware persistence implementation.
export function save(server: Server, credential: string, operation_id = "") {
  const path = resolve(server.data, "synthetic-client.json");
  const temporary = path + ".new";
  const fd = openSync(temporary, "w", 0o600);
  try { writeFileSync(fd, JSON.stringify({ credential, operation_id })); fsyncSync(fd); }
  finally { closeSync(fd); }
  renameSync(temporary, path);
  const dir = openSync(server.data, "r");
  try { fsyncSync(dir); } finally { closeSync(dir); }
  const saved = JSON.parse(readFileSync(path, "utf8"));
  expect(saved.credential === credential && saved.operation_id === operation_id).toBe(true);
  return saved.credential as string;
}

export async function socket(server: Server, path: string, frame: object) {
  const ws = new WebSocket(server.origin.replace("http:", "ws:") + path);
  const messages: Record<string, unknown>[] = [];
  ws.addEventListener("message", event => {
    if (typeof event.data === "string") messages.push(JSON.parse(event.data));
  });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => { ws.close(); reject(new Error("Socket startup timed out")); }, 5000);
    ws.addEventListener("open", () => { clearTimeout(timer); ws.send(JSON.stringify(frame)); resolve(); }, { once: true });
    ws.addEventListener("error", () => { clearTimeout(timer); reject(new Error("Socket startup failed")); }, { once: true });
  });
  await expect.poll(() => messages.length).toBeGreaterThan(0);
  return { ws, messages };
}

export async function pairDevice(server: Server, integration: string) {
  const keys = generateKeyPairSync("ed25519");
  const post = async (action: string, body: object, token?: string) => {
    const response = await native(server, `/api/enrollment/v1/${action}`, body, token);
    expect(response.status).toBe(200);
    return response.json();
  };
  const challenge = await post("request", { kind: "physical", display_name: "Synthetic speaker",
    public_key: keys.publicKey.export({ format: "der", type: "spki" }).subarray(-32).toString("hex") });
  const signature = (action: string) => sign(null, Buffer.from(JSON.stringify([
    "vauxr-enrollment", action, ...["version", "request_id", "server_id", "origin", "kind",
      "public_key", "device_id", "nonce", "expires_at", "owner_generation", "display_name"].map(k => challenge[k]),
  ])), keys.privateKey).toString("hex");
  const proof = await post("prove", { request_id: challenge.request_id, signature: signature("prove") });
  for (const action of ["initiate", "approve"]) {
    const approval = await post(action, { request_id: challenge.request_id, code: proof.code }, integration);
    expect(Object.keys(approval).sort()).toEqual(["device_id", "status"]);
  }
  return post("redeem", { request_id: challenge.request_id, signature: signature("redeem") });
}
