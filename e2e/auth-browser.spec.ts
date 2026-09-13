// Isolated real aiohttp + clean Chromium. Synthetic clients only; no physical hardware.
import { test, expect } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { createServer } from "node:https";
import { networkInterfaces } from "node:os";
import { generateKeyPairSync, sign } from "node:crypto";
const root = resolve(import.meta.dirname, "..");
const origin = "http://localhost:18080";
let server: ChildProcess;
let data: string;
let environment: NodeJS.ProcessEnv;
function consoleCode(recover = false) {
  // Code stays inside test memory; never include in command arguments or output artifacts.
  return execFileSync(
    "python3",
    [
      "-c",
      `from pathlib import Path; import os; from auth_store import CredentialStore; from owner_auth import OwnerAuth; o=OwnerAuth(CredentialStore(Path(os.environ['DATA_DIR'])/'authz.json')); print(o.console_claim(recover=${recover ? "True" : "False"}))`,
    ],
    { cwd: root, env: environment, encoding: "utf8" },
  ).trim();
}
test.use({ baseURL: origin, trace: "off", screenshot: "off", video: "off" });
test.beforeAll(async () => {
  data = mkdtempSync(resolve(root, ".browser-test-"));
  // Synthetic unavailable Wyoming providers exercise selection without model inference.
  writeFileSync(resolve(data, "speech-providers.json"), JSON.stringify([
    { id: "parakeet", kind: "stt", adapter: "parakeet-v3", model: "v3", host: "127.0.0.1", port: 1 },
    { id: "kokoro", kind: "tts", adapter: "kokoro", model: "kokoro-test", host: "127.0.0.1", port: 1, voices: ["af", "bf"] },
  ]));
  environment = {
    ...process.env,
    PYTHONPATH: resolve(root, "src"),
    DATA_DIR: data,
    OWNER_HTTP_ORIGIN: origin,
    HTTP_PORT: "18080",
    WS_PORT: "18765",
  };
  delete environment.OPERATOR_TOKEN;
  delete environment.OWNER_HTTPS_ORIGIN;
  delete environment.OWNER_TRUSTED_PROXIES;
  server = spawn("python3", ["-m", "server"], {
    cwd: root,
    env: environment,
    stdio: "ignore",
  });
  await expect
    .poll(async () => {
      try {
        return (await fetch(origin + "/api/auth/status")).status;
      } catch {
        return 0;
      }
    })
    .toBe(200);
});
test.afterAll(async () => {
  server?.kill("SIGTERM");
  if (server && server.exitCode === null)
    await new Promise((r) => server.once("exit", r));
  rmSync(data, { recursive: true, force: true });
});
test("owner setup, signed pairing, scoped browser lifecycle, tabs, reload and logout", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  await page.goto("/");
  await expect(page.getByRole("main")).toHaveCount(0);
  expect((await context.request.get("/api/devices")).status()).toBe(401);
  const field = page.getByLabel("Operator token or console code");
  await field.fill(consoleCode());
  await page.getByText("Submit console setup/recovery code").click();
  const token = await page.locator("output").textContent();
  expect(Boolean(token?.startsWith("vx_op_"))).toBe(true);
  expect(
    (
      await context.request.post("/api/auth/login", {
        headers: { Origin: origin },
        data: { operator_token: token },
      })
    ).status(),
  ).toBe(400);
  await page.getByText("I saved this in my password manager").click();
  await expect(page.locator("output")).toHaveCount(0);
  await field.fill(token!);
  await page.getByText("Log in with saved operator token").click();
  await expect(page.getByRole("main")).toBeVisible();
  const cookie = (await context.cookies()).find(
    (c) => c.name === "vauxr_owner",
  )!;
  expect(
    cookie.httpOnly && cookie.sameSite === "Strict" && !cookie.secure,
  ).toBe(true);
  const csrf = (await (await context.request.get("/api/auth/session")).json())
    .csrf_token;
  expect(
    (
      await context.request.post("/api/lifecycle/v1/rotate", {
        headers: { Origin: origin },
        data: {},
      })
    ).status(),
  ).toBe(403);
  expect(
    (
      await context.request.post("/api/auth/logout", {
        headers: { Origin: "http://evil.invalid", "X-CSRF-Token": csrf },
        data: {},
      })
    ).status(),
  ).toBe(403);
  const post = async (path: string, body: object = {}) => {
    const res = await context.request.post(path, {
      headers: { Origin: origin, "X-CSRF-Token": csrf },
      data: body,
    });
    expect(res.status()).toBe(200);
    return res.json();
  };
  // Simulated physical client uses the frozen Ed25519 transcript. No claim of real button/audio.
  const keys = generateKeyPairSync("ed25519");
  const raw = keys.publicKey
    .export({ format: "der", type: "spki" })
    .subarray(-32);
  const challenge = await post("/api/enrollment/v1/request", {
    kind: "physical",
    public_key: raw.toString("hex"),
    display_name: "Synthetic speaker",
  });
  const signed = (action: string) =>
    sign(
      null,
      Buffer.from(
        JSON.stringify([
          "vauxr-enrollment",
          action,
          ...[
            "version",
            "request_id",
            "server_id",
            "origin",
            "kind",
            "public_key",
            "device_id",
            "nonce",
            "expires_at",
            "owner_generation",
            "display_name",
          ].map((k) => challenge[k]),
        ]),
      ),
      keys.privateKey,
    ).toString("hex");
  const proof = await post("/api/enrollment/v1/prove", {
    request_id: challenge.request_id,
    signature: signed("prove"),
  });
  await page.getByText("Refresh pairing and identities").click();
  const row = page
    .locator("div.border-t")
    .filter({ hasText: "Synthetic speaker" })
    .first();
  await row.getByLabel("Spoken eight-digit code").fill(proof.code);
  await expect(row.getByText("Initiate matching speaker")).toBeDisabled();
  await row.getByRole("checkbox").check();
  await row.getByText("Initiate matching speaker").click();
  await expect(row.getByText("Approve matching speaker")).toBeVisible();
  await row.getByLabel("Spoken eight-digit code").fill(proof.code);
  await row.getByRole("checkbox").check();
  await row.getByText("Approve matching speaker").click();
  await expect(row.getByText("Deny request")).toBeVisible();
  const physical = await post("/api/enrollment/v1/redeem", {
    request_id: challenge.request_id,
    signature: signed("redeem"),
  });
  expect(await page.content()).not.toContain(physical.device_token);
  // Owner bearer cannot become a voice identity.
  const denied = await page.evaluate(
    ({ token }) =>
      new Promise<boolean>((resolve) => {
        const ws = new WebSocket("ws://localhost:18080/ws");
        ws.onopen = () =>
          ws.send(JSON.stringify({ type: "hello", device_id: "spoof", token }));
        ws.onmessage = (e) => {
          const m = JSON.parse(e.data);
          if (m.type === "error") {
            ws.close();
            resolve(true);
          }
        };
        ws.onclose = () => resolve(false);
        setTimeout(() => {
          ws.close();
          resolve(false);
        }, 2000);
      }),
    { token },
  );
  expect(denied).toBe(true);
  const frames: string[] = [];
  page.on("websocket", (ws) =>
    ws.on("framesent", (frame) => {
      if (typeof frame.payload === "string") frames.push(frame.payload);
    }),
  );
  await page.getByLabel("Voice WebSocket URL").fill("ws://localhost:18080/ws");
  await page.getByText("Connect browser voice", { exact: true }).click();
  await expect(
    page.getByText("Connected", { exact: true }).first(),
  ).toBeVisible();
  const identity = await page.evaluate(
    async () =>
      new Promise<{ deviceId: string; token: string; extractable: boolean }>(
        (resolve) => {
          const r = indexedDB.open("vauxr-browser-v1");
          r.onsuccess = () => {
            const q = r.result
              .transaction("identity")
              .objectStore("identity")
              .get("current");
            q.onsuccess = () => {
              const v = q.result;
              resolve({
                deviceId: v.deviceId,
                token: v.token,
                extractable: v.privateKey.extractable,
              });
              r.result.close();
            };
          };
        },
      ),
  );
  expect(identity.extractable).toBe(false);
  expect(identity.deviceId).not.toBe(physical.device_id);
  expect(
    frames.some(
      (f) =>
        JSON.parse(f).type === "hello" &&
        JSON.parse(f).token === identity.token,
    ),
  ).toBe(true);
  expect(frames.some((f) => f.includes(token!))).toBe(false);
  expect(
    await page.evaluate(
      async (token) =>
        (
          await fetch("/api/devices", {
            credentials: "omit",
            headers: { Authorization: `Bearer ${token}` },
          })
        ).status,
      identity.token,
    ),
  ).toBe(403);
  const tab = await context.newPage();
  await tab.goto("/");
  await tab.getByLabel("Voice WebSocket URL").fill("ws://localhost:18080/ws");
  await tab.getByText("Connect browser voice", { exact: true }).click();
  await expect(tab.getByRole("alert")).toContainText("another tab");
  // Permission denial does not send voice.start or start a turn.
  await page.evaluate(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException("Permission denied", "NotAllowedError");
    };
  });
  const talk = page.getByRole("button", { name: /hold to talk/i });
  await talk.dispatchEvent("mousedown");
  await expect(page.getByText(/Microphone capture failed/)).toBeVisible();
  expect(frames.some((f) => JSON.parse(f).type === "voice.start")).toBe(false);
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  // Offline rotation UI: retain queued state, then connect to deliver/save/ACK.
  await page.getByText("Known offline identity").click();
  await page.getByLabel("Subject ID").fill(identity.deviceId);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByText("rotate known identity").click();
  await expect(
    page.getByText("Queued; may be offline. No replacement saved."),
  ).toBeVisible();
  await page.getByText("Connect browser voice", { exact: true }).click();
  await expect(
    page.getByText("Connected", { exact: true }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Refresh status", exact: true })
    .click();
  await expect(
    page.getByText("Completed after durable-save acknowledgement."),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByRole("main")).toBeVisible();
  await expect(
    page.getByText("Connect browser voice", { exact: true }),
  ).toBeVisible();
  await post("/api/lifecycle/v1/revoke", {
    operation_id: "77777777777777777777777777777777",
    role: "device",
    subject: identity.deviceId,
  });
  await page.getByText("Connect browser voice", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Request failed");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByText("Recover browser identity", { exact: true }).click();
  await expect(
    page.getByText("Connected", { exact: true }).first(),
  ).toBeVisible();
  // Hold tab B's successful session body until tab A's logout broadcast arrives.
  await tab.addInitScript(() => {
    const originalFetch = window.fetch;
    (window as any).delayedSession = {};
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (args[0] === "/api/auth/session" && response.ok) {
        const body = await response.json();
        (window as any).delayedSession.ready = true;
        response.json = async () => {
          await new Promise<void>((resolve) => {
            (window as any).delayedSession.release = resolve;
          });
          (window as any).delayedSession.consumed = true;
          return body;
        };
      }
      return response;
    };
    const channel = new BroadcastChannel("vauxr-owner");
    channel.onmessage = () => { (window as any).delayedSession.logout = true; };
  });
  await tab.reload();
  await expect.poll(() => tab.evaluate(() => Boolean((window as any).delayedSession.release))).toBe(true);
  await page.getByText("Log out on this browser").click();
  await expect(page.getByRole("main")).toHaveCount(0);
  await expect(tab.getByRole("main")).toHaveCount(0);
  await expect.poll(() => tab.evaluate(() => (window as any).delayedSession.logout)).toBe(true);
  await tab.evaluate(async () => {
    (window as any).delayedSession.release();
    // Flush promise continuations and React's render before checking for resurrection.
    await new Promise(requestAnimationFrame);
    await new Promise(requestAnimationFrame);
  });
  expect(await tab.evaluate(() => (window as any).delayedSession.consumed)).toBe(true);
  await expect(tab.getByRole("main")).toHaveCount(0);

  expect(
    await page.evaluate(
      async () =>
        new Promise<boolean>((resolve) => {
          const r = indexedDB.open("vauxr-browser-v1");
          r.onsuccess = () => {
            const q = r.result
              .transaction("identity")
              .objectStore("identity")
              .get("current");
            q.onsuccess = () => {
              resolve(
                q.result.token === undefined && q.result.deviceId !== undefined,
              );
              r.result.close();
            };
          };
        }),
    ),
  ).toBe(true);
  const residue = await page.evaluate(async () => ({
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
    url: location.href,
    body: document.body.textContent,
  }));
  expect(JSON.stringify(residue).includes(token!)).toBe(false);
  expect(JSON.stringify(residue).includes(identity.token)).toBe(false);
  expect((await context.request.get("/api/devices")).status()).toBe(401);
  // Console recovery replaces owner access and survives the UI's separate claim/save/login steps.
  await field.fill(consoleCode(true));
  await page.getByText("Submit console setup/recovery code").click();
  await expect(page.locator("output")).toBeVisible();
  const replacement = await page.locator("output").textContent();
  await page.getByText("I saved this in my password manager").click();
  await field.fill(replacement!);
  await page.getByText("Log in with saved operator token").click();
  await expect(page.getByRole("main")).toBeVisible();
});

test("non-loopback HTTP administration works while browser microphone remains restricted", async ({
  page,
}) => {
  const address = Object.values(networkInterfaces())
    .flat()
    .find((i) => i?.family === "IPv4" && !i.internal)?.address;
  test.skip(!address, "No non-loopback interface available");
  server.kill("SIGTERM");
  await new Promise((r) => server.once("exit", r));
  const lan = `http://${address}:18080`;
  environment.OWNER_HTTP_ORIGIN = lan;
  server = spawn("python3", ["-m", "server"], {
    cwd: root,
    env: environment,
    stdio: "ignore",
  });
  await expect
    .poll(async () => {
      try {
        return (await fetch(lan + "/api/auth/status")).status;
      } catch {
        return 0;
      }
    })
    .toBe(200);
  await page.goto(lan);
  await page
    .getByLabel("Operator token or console code")
    .fill(consoleCode(true));
  await page.getByText("Submit console setup/recovery code").click();
  const token = await page.locator("output").textContent();
  await page.getByText("I saved this in my password manager").click();
  await page.getByLabel("Operator token or console code").fill(token!);
  await page.getByText("Log in with saved operator token").click();
  await expect(page.getByRole("main")).toBeVisible();
  expect(await page.evaluate(() => isSecureContext)).toBe(false);
  await expect(
    page.getByText("Microphone unavailable.", { exact: true }),
  ).toBeVisible();
  await page.getByText("Connect browser voice", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "HTTP administration remains available",
  );
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Devices", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Webhooks", exact: true }),
  ).toBeVisible();
  const speechRequests: { url: string; authorization?: string; csrf?: string }[] = [];
  page.on("request", request => {
    if (request.url().endsWith("/speech")) speechRequests.push({
      url: request.url(), authorization: request.headers()["authorization"],
      csrf: request.headers()["x-csrf-token"],
    });
  });
  const globalSpeech = page.getByRole("region", { name: "Global speech", exact: true });
  await expect(globalSpeech.getByLabel("TTS backend")).toBeVisible();
  await globalSpeech.getByLabel("STT backend").selectOption("parakeet");
  await expect(globalSpeech.getByLabel("TTS backend")).toBeEnabled();
  await globalSpeech.getByLabel("TTS backend").selectOption("kokoro");
  await expect(globalSpeech.getByText("Effective: parakeet / kokoro / af")).toBeVisible();
  await expect(globalSpeech.getByRole("option", { name: "kokoro · kokoro-test · unavailable" })).toBeAttached();
  await globalSpeech.getByRole("combobox", { name: /^Voice/ }).selectOption("bf");
  await expect(globalSpeech.getByText("Effective: parakeet / kokoro / bf")).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(globalSpeech.getByText("Effective: parakeet / kokoro / bf")).toBeVisible();
  // Serve an offline device projection to expose the existing expandable device card;
  // speech GET/PATCH still reach the real isolated server and persistent store.
  await page.route("**/api/devices", route => route.fulfill({ json: [{
    id: "speech-offline", name: "Speech offline device", state: "idle",
    lastSeen: new Date().toISOString(), config: {},
  }] }));
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  await page.getByText("Speech offline device", { exact: true }).click();
  const deviceSpeech = page.getByRole("region", { name: "Device speech", exact: true });
  await expect(deviceSpeech.getByText("Effective: parakeet / kokoro / bf")).toBeVisible();
  await expect(deviceSpeech.getByLabel("TTS backend")).toHaveValue("");
  await deviceSpeech.getByRole("combobox", { name: /^Voice/ }).selectOption("af");
  await expect(deviceSpeech.getByText("Effective: parakeet / kokoro / af")).toBeVisible();
  await deviceSpeech.getByLabel("TTS backend").selectOption("piper");
  await expect(deviceSpeech.getByRole("combobox", { name: /^Voice/ })).not.toContainText("af");
  await expect(deviceSpeech.getByRole("button", { name: "Reset to defaults" })).toBeEnabled();
  await deviceSpeech.getByRole("button", { name: "Reset to defaults" }).click();
  await expect(deviceSpeech.getByText("Effective: parakeet / kokoro / bf")).toBeVisible();
  expect(speechRequests.length).toBeGreaterThan(6);
  expect(speechRequests.every(r => r.url.startsWith(lan + "/api/") && !r.authorization && Boolean(r.csrf))).toBe(true);
  await page.getByText("Log out on this browser").click();
  await expect(page.getByRole("main")).toHaveCount(0);
});

test("Chromium rejects an untrusted HTTPS/WSS server without certificate bypass", async ({
  page,
}) => {
  execFileSync(
    "openssl",
    [
      "req",
      "-x509",
      "-newkey",
      "rsa:2048",
      "-nodes",
      "-keyout",
      resolve(data, "test.key"),
      "-out",
      resolve(data, "test.crt"),
      "-days",
      "1",
      "-subj",
      "/CN=localhost",
      "-addext",
      "subjectAltName=DNS:localhost",
    ],
    { stdio: "ignore" },
  );
  let received = 0;
  const tls = createServer(
    {
      key: readFileSync(resolve(data, "test.key")),
      cert: readFileSync(resolve(data, "test.crt")),
    },
    (_req, res) => {
      received++;
      res.end("untrusted");
    },
  );
  await new Promise<void>((resolve) => tls.listen(18443, "127.0.0.1", resolve));
  try {
    await expect(page.goto("https://localhost:18443")).rejects.toThrow(
      /ERR_CERT_AUTHORITY_INVALID/,
    );
    const socketPage = await page.context().newPage();
    const rejected = await socketPage.evaluate(
      () =>
        new Promise<boolean>((resolve) => {
          const ws = new WebSocket("wss://localhost:18443/ws");
          ws.onerror = () => resolve(true);
          ws.onopen = () => {
            ws.close();
            resolve(false);
          };
          setTimeout(() => {
            ws.close();
            resolve(false);
          }, 3000);
        }),
    );
    await socketPage.close();
    expect(rejected).toBe(true);
    expect(received).toBe(0);
  } finally {
    await new Promise<void>((resolve) => tls.close(() => resolve()));
  }
});
