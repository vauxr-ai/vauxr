import { test, expect, login, native, client, requestIntegration, save, socket, pairDevice } from "./auth-fixture";

test("owner approves integration; scoped pairing, speech, activation, rotation and revoke remain separate", async ({ page, context, server }) => {
  test.setTimeout(60000);
  const operator = await login(page, server);
  page.on("dialog", dialog => dialog.accept());
  const csrf = (await (await context.request.get(server.origin + "/api/auth/session")).json()).csrf_token;
  const owner = async (path: string, body: object = {}) => {
    const response = await context.request.post(server.origin + path, {
      headers: { Origin: server.origin, "X-CSRF-Token": csrf }, data: body,
    });
    expect(response.status()).toBe(200);
    return response.json();
  };
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await expect(page.getByRole("button", { name: /Add agent|Create agent|Rotate token/ })).toHaveCount(0);
  await expect(page.getByLabel(/device token|shared token|agent token/i)).toHaveCount(0);
  const { secret, request } = await requestIntegration(server);
  const enrollment = page.getByRole("region", { name: "Integration enrollment", exact: true });
  await enrollment.getByText("Refresh integration requests").click();
  const row = enrollment.getByRole("region", { name: `Integration request ${request.request_id}`, exact: true });
  await expect(row).toContainText(server.origin);
  await expect(row).not.toContainText(request.user_code);
  await row.getByLabel("Integration matching code").fill(request.user_code);
  await expect(row.getByText("Approve matching integration")).toBeDisabled();
  await row.getByRole("checkbox").check();
  // Server independently enforces CSRF, exact Origin and Host.
  const body = { request_id: request.request_id, user_code: request.user_code };
  for (const headers of [
    { Origin: server.origin },
    { Origin: "http://foreign.invalid", "X-CSRF-Token": csrf },
    { Origin: server.origin, "X-CSRF-Token": csrf, Host: "foreign.invalid" },
  ]) {
    expect((await context.request.post(server.origin + "/api/integrations/v1/approve", { headers, data: body })).status()).toBe(403);
  }
  await row.getByText("Approve matching integration").click();
  await expect(row).toContainText("State: approved");
  const delivery = await client(server, "deliver", secret);
  expect(Boolean(delivery.save_required && delivery.credential.startsWith("vx_int_"))).toBe(true);
  expect((await native(server, "/api/devices", undefined, delivery.credential)).status).toBe(401);
  expect((await native(server, "/api/integrations/v1/deliver", secret)).status).toBe(409);
  await enrollment.getByText("Refresh integration requests").click();
  await expect(row).toContainText("durable save has NOT been acknowledged");
  const integration = save(server, delivery.credential);
  await client(server, "ack", { ...secret, credential: integration, saved: true });
  await client(server, "ack", { ...secret, credential: integration, saved: true }); // lost-ACK retry
  await enrollment.getByText("Refresh integration requests").click();
  await expect(row).toContainText("State: completed");
  expect((await native(server, "/api/devices", undefined, integration)).status).toBe(200);
  for (const path of ["/api/agents", "/api/webhooks", "/api/speech", "/api/devices/offline/speech"]) {
    expect((await native(server, path, undefined, integration)).status).toBe(403);
  }
  // Neither owner bearer nor integration credentials can impersonate a device.
  for (const token of [operator, integration]) {
    const denied = await socket(server, "/ws", { type: "hello", device_id: "spoof", token });
    expect(denied.messages[0].type).toBe("error");
    denied.ws.close();
  }
  expect((await native(server, "/api/devices", undefined, operator)).status).toBe(401);
  expect((await native(server, "/api/lifecycle/v1/revoke", {
    operation_id: "1".repeat(32), role: "integration", subject: request.agent_id,
  }, integration)).status).toBe(403);
  expect((await native(server, "/api/enrollment/v1/request", {
    kind: "browser", public_key: "1".repeat(64), display_name: "Spoof browser",
  }, integration)).status).toBe(400);

  // A synthetic physical client proves its key and matches its code through integration authority.
  const device = await pairDevice(server, integration);
  const speaker = await socket(server, "/ws", { type: "hello", device_id: device.device_id, token: device.device_token });
  expect(speaker.messages[0].type).toBe("hello");
  const agent = await socket(server, "/agent", { type: "agent.auth", token: integration });
  expect(agent.messages[0].type).toBe("agent.ready");
  expect(agent.messages[0].agentId).toBe(request.agent_id);
  try {
    const deviceAgent = await socket(server, "/agent", { type: "agent.auth", token: device.device_token });
    expect(deviceAgent.messages[0].type).toBe("error");
    deviceAgent.ws.close();
    expect((await native(server, "/api/speech", undefined, device.device_token)).status).toBe(403);
    await page.getByText("Refresh agents", { exact: true }).click();
    const route = page.locator("div").filter({ hasText: /^Synthetic OpenClaw — openclaw — Inactive route/ }).last();
    await route.getByRole("button", { name: "Activate", exact: true }).click();
    await expect(page.getByText(/Synthetic OpenClaw — openclaw — Active route/)).toBeVisible();
    // PR58 settings run against the actual connected device projection and real speech API.
    await page.getByRole("button", { name: "Devices", exact: true }).click();
    await page.getByText(device.device_id, { exact: true }).first().click();
    const speech = page.getByRole("region", { name: "Device speech", exact: true });
    await expect(speech.getByLabel("TTS backend")).toBeVisible();
    await speech.getByLabel("TTS backend").selectOption("piper");
    await expect(speech.getByRole("button", { name: "Reset to defaults" })).toBeEnabled();
    await speech.getByRole("button", { name: "Reset to defaults" }).click();
    await expect(speech.getByLabel("TTS backend")).toHaveValue("");
    // Owner lifecycle is public metadata only; native client alone delivers and saves the replacement.
    await page.getByRole("button", { name: "Connection", exact: true }).click();
    await page.getByText("Refresh pairing and identities").click();
    const access = page.locator("div").filter({ hasText: `Synthetic OpenClaw — ${request.agent_id}` })
      .filter({ has: page.getByRole("button", { name: "rotate integration", exact: true }) }).last();
    await access.getByText("rotate integration", { exact: true }).click();
    await expect(page.getByText("Queued; may be offline. No replacement saved.")).toBeVisible();
    const retained = await page.evaluate(() => JSON.parse(sessionStorage.getItem("vauxr-operations") || "[]"));
    const operation = retained.find((r: { subject: string; action: string }) => r.subject === request.agent_id && r.action === "rotate");
    expect(operation.state).toBe("queued");
    const lifecycle = async (action: string, token: string, body: object = {}) => {
      const response = await native(server, `/api/lifecycle/v1/${action}`, body, token);
      expect(response.status).toBe(200);
      return response.json();
    };
    expect((await lifecycle("poll", integration)).state).toBe("pending");
    const replacement = await lifecycle("deliver", integration, { operation_id: operation.operation_id });
    const rotated = save(server, replacement.credential, operation.operation_id);
    const wrongAck = await native(server, "/api/lifecycle/v1/ack", {
      operation_id: operation.operation_id, saved: true,
    }, integration);
    expect(wrongAck.status).toBe(400);
    expect((await wrongAck.json()).error).toBe("invalid_ack");
    await lifecycle("ack", rotated, { operation_id: operation.operation_id, saved: true });
    await expect.poll(() => agent.ws.readyState).toBe(WebSocket.CLOSED);
    expect((await native(server, "/api/devices", undefined, integration)).status).toBe(401);
    const replacementAgent = await socket(server, "/agent", { type: "agent.auth", token: rotated });
    expect(replacementAgent.messages[0].type).toBe("agent.ready");
    try {
      await access.getByText("revoke integration", { exact: true }).click();
      await expect.poll(() => replacementAgent.ws.readyState).toBe(WebSocket.CLOSED);
      expect((await native(server, "/api/devices", undefined, rotated)).status).toBe(401);
      expect(speaker.ws.readyState).toBe(WebSocket.OPEN);
      // Device still authenticates while both integration generations stay retired.
      expect((await native(server, "/api/lifecycle/v1/poll", {}, device.device_token)).status).toBe(200);
      const routes = await (await context.request.get(server.origin + "/api/agents")).json();
      expect(routes.some((r: { id: string }) => r.id === request.agent_id)).toBe(false);
      const publicRequests = await owner("/api/integrations/v1/list");
      expect(publicRequests.requests.find((r: { request_id: string }) => r.request_id === request.request_id).state).toBe("revoked");
      const publicText = JSON.stringify(publicRequests) + await page.content();
      expect([operator, integration, rotated, device.device_token, secret.request_secret].some(s => publicText.includes(s))).toBe(false);
      const storage = await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]));
      expect([operator, integration, rotated, device.device_token, secret.request_secret].some(s => storage.includes(s))).toBe(false);
    } finally { replacementAgent.ws.close(); }
  } finally { speaker.ws.close(); agent.ws.close(); }
});

test("matching code errors and denial never deliver credentials; legacy forms stay absent after reload", async ({ page, server }) => {
  await login(page, server);
  page.on("dialog", dialog => dialog.accept());
  const first = await requestIntegration(server, "Same display name");
  const second = await requestIntegration(server, "Same display name");
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByText("Refresh integration requests").click();
  const row = page.getByRole("region", { name: `Integration request ${first.request.request_id}`, exact: true });
  await row.getByLabel("Integration matching code").fill(second.request.user_code);
  await row.getByRole("checkbox").check();
  await row.getByText("Approve matching integration").click();
  await expect(page.getByRole("alert")).toContainText("Request failed");
  await expect(row.getByLabel("Integration matching code")).toHaveValue("");
  expect((await client(server, "status", first.secret)).state).toBe("pending");
  await row.getByText("Deny integration request", { exact: true }).click();
  await expect(row).toContainText("State: denied");
  expect((await native(server, "/api/integrations/v1/deliver", first.secret)).status).toBe(409);
  expect((await client(server, "status", second.secret)).state).toBe("pending");
  await client(server, "cancel", second.secret);
  await page.reload();
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByText("Refresh integration requests").click();
  await expect(row).toContainText("State: denied");
  await expect(page.getByRole("region", { name: `Integration request ${second.request.request_id}`, exact: true })).toContainText("State: cancelled");
  await expect(page.getByRole("button", { name: /Add agent|Create agent|Rotate token/ })).toHaveCount(0);
  await expect(page.getByLabel(/shared token|device token|agent token/i)).toHaveCount(0);
});

test("server restart preserves owner session, approved integration and speech configuration", async ({ page, context, server }) => {
  await login(page, server);
  const { secret, request } = await requestIntegration(server);
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByText("Refresh integration requests").click();
  const row = page.getByRole("region", { name: `Integration request ${request.request_id}`, exact: true });
  await row.getByLabel("Integration matching code").fill(request.user_code);
  await row.getByRole("checkbox").check();
  await row.getByText("Approve matching integration").click();
  await expect(row).toContainText("State: approved");
  const delivery = await client(server, "deliver", secret);
  const credential = save(server, delivery.credential);
  await client(server, "ack", { ...secret, credential, saved: true });
  const session = await (await context.request.get(server.origin + "/api/auth/session")).json();
  const csrf = session.csrf_token;
  const speechPath = server.origin + "/api/devices/retained-offline/speech";
  expect((await context.request.patch(speechPath, {
    headers: { Origin: server.origin, "X-CSRF-Token": csrf }, data: { tts_backend: "piper" },
  })).status()).toBe(200);
  const before = await (await context.request.get(speechPath)).json();
  await server.restart();
  const restartedSession = await context.request.get(server.origin + "/api/auth/session");
  expect(restartedSession.status()).toBe(200);
  expect(await restartedSession.json()).toEqual(session);
  expect((await context.request.get(speechPath)).status()).toBe(200);
  expect((await native(server, "/api/devices", undefined, credential)).status).toBe(200);
  expect((await client(server, "ack", { ...secret, credential, saved: true })).state).toBe("completed");
  await page.reload();
  await expect(page.getByRole("main")).toBeVisible();
  expect(await (await context.request.get(speechPath)).json()).toEqual(before);
  const connection = await socket(server, "/agent", { type: "agent.auth", token: credential });
  expect(connection.messages[0].type).toBe("agent.ready");
  connection.ws.close();
});
