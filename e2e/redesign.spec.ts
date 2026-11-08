import { closeAgentDetail } from "./auth-fixture";
import { openIntegrations, openSettings } from "./auth-fixture";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import {
  test,
  expect,
  login,
  requestIntegration,
  save,
  client,
  native,
  pairDevice,
  socket,
} from "./auth-fixture";
const artifacts = process.env.VAUXR_B2_ARTIFACT_DIR;
// Explicit captures only after owner login; never capture credential/setup pages.
async function screenshot(page: Page, name: string) {
  if (!artifacts) return;
  mkdirSync(artifacts, { recursive: true });
  await page.screenshot({ path: resolve(artifacts, `${name}.png`) });
}

test("B2 keeps administration independent, browser ownership stable, and microphone explicit", async ({
  page,
  server,
}) => {
  await page.addInitScript(() => {
    (window as any).micCalls = 0;
    navigator.mediaDevices.getUserMedia = async () => {
      (window as any).micCalls++;
      throw new DOMException("Test permission denial", "NotAllowedError");
    };
  });
  await login(page, server);
  await expect(
    page.getByRole("button", { name: "Expand voice companion" }),
  ).toBeVisible();
  for (const name of ["Devices", "Settings", "Agents"])
    await page.getByRole("button", { name, exact: true }).click();
  expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
  let sockets = 0;
  page.on("websocket", () => sockets++);
  await closeAgentDetail(page);
  await page
    .getByRole("button", { name: "Connect browser", exact: true })
    .click();
  const talk = page.getByRole("complementary", { name: "Talk panel" });
  await expect(
    talk.getByRole("radio", { name: "Standard push-to-talk" }),
  ).toBeEnabled();
  expect(sockets).toBe(1);
  expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
  for (const name of ["Devices", "Settings", "Agents"]) {
    await page.getByRole("button", { name, exact: true }).click();
    await expect(
      talk.getByRole("button", { name: "Hold to Talk" }),
    ).toBeEnabled();
  }
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  const device = page.locator("main li").first();
  await device.getByRole("button").first().click();
  await device.getByLabel("Double press action").selectOption("command");
  await expect(device.getByLabel("Double press command")).toHaveValue("mute");
  await device
    .getByLabel("Name", { exact: true })
    .fill("Browser acceptance device");
  await device.getByLabel("Name", { exact: true }).blur();
  await expect(
    device.getByText("Browser acceptance device", { exact: true }),
  ).toBeVisible();
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  await page.getByText("Browser acceptance device", { exact: true }).click();
  await expect(page.getByLabel("Double press command")).toHaveValue("mute");
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Collapse voice companion" }).click();
  await expect(
    talk.getByRole("button", { name: "Hold to Talk" }),
  ).toBeVisible();
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Expand voice companion" }).click();
  expect(sockets).toBe(1);
  const button = talk.getByRole("button", { name: "Hold to Talk" });
  await button.dispatchEvent("mousedown");
  await expect
    .poll(() => page.evaluate(() => (window as any).micCalls))
    .toBe(1);
  await expect(
    page.getByRole("alert").filter({ hasText: "Microphone capture failed" }),
  ).toBeVisible();
  await button.dispatchEvent("mouseup");
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
});

test("B2 themes, reserved desktop companion, phone focus and authenticated screenshots", async ({
  page,
  server,
}) => {
  await login(page, server);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const appearance = page.getByLabel("Appearance");
  for (const viewport of [
    { width: 1366, height: 900 },
    { width: 390, height: 844 },
  ]) {
    await page.setViewportSize(viewport);
    for (const theme of ["light", "dark", "system"]) {
      await page.emulateMedia({ colorScheme: "dark" });
      await appearance.selectOption(theme);
      await expect(page.locator("html")).toHaveAttribute(
        "data-theme",
        theme === "system" ? "dark" : theme,
      );
      const size = viewport.width > 760 ? "desktop" : "phone";
      await screenshot(page, `${size}-${theme}-agents`);
      await closeAgentDetail(page);
      await page
        .getByRole("button", { name: "Expand voice companion" })
        .click();
      if (size === "desktop") {
        const main = await page.getByRole("main").boundingBox();
        const companion = await page
          .getByRole("region", { name: "Voice companion", exact: true })
          .boundingBox();
        expect(main!.x + main!.width).toBeLessThanOrEqual(companion!.x);
      } else {
        expect(
          await page
            .getByRole("main")
            .evaluate((el) => (el as HTMLElement).inert),
        ).toBe(true);
        await page
          .getByRole("button", { name: "Collapse voice companion" })
          .focus();
        await page.keyboard.press("Shift+Tab");
        await expect(
          page.getByText("Browser voice connection", { exact: true }),
        ).toBeFocused();
        await page.keyboard.press("Tab");
        await expect(
          page.getByRole("button", { name: "Collapse voice companion" }),
        ).toBeFocused();
      }
      await screenshot(page, `${size}-${theme}-companion`);
      await page.keyboard.press("Escape");
      await expect(
        page.getByRole("button", { name: "Expand voice companion" }),
      ).toBeFocused();
      expect(
        await page
          .getByRole("main")
          .evaluate((el) => (el as HTMLElement).inert),
      ).toBe(false);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  }
  await page.setViewportSize({ width: 1366, height: 900 });
  await closeAgentDetail(page);
  await page
    .getByRole("button", { name: "Connect browser", exact: true })
    .click();
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Devices", exact: true }).click();
  await page.locator("main li").first().getByRole("button").first().click();
  await expect(
    page.getByRole("region", { name: "Device speech", exact: true }),
  ).toBeVisible();
  for (const size of ["desktop", "phone"]) {
    await page.setViewportSize(
      size === "desktop"
        ? { width: 1366, height: 900 }
        : { width: 390, height: 844 },
    );
    for (const theme of ["light", "dark", "system"]) {
      if (size === "phone") await page.keyboard.press("Escape");
      await appearance.selectOption(theme);
      if (size === "phone") {
        await screenshot(page, `${size}-${theme}-device`);
        expect(
          await page
            .getByRole("main")
            .evaluate((el) => el.scrollWidth <= el.clientWidth),
        ).toBe(true);
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Expand voice companion" })
          .click();
      }
      await screenshot(page, `${size}-${theme}-device-companion`);
    }
  }
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(appearance).toHaveValue("system");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("phone access panel wraps long device identities without horizontal scrolling", async ({
  page,
  server,
}) => {
  await login(page, server);
  const deviceId = "device_" + "0123456789abcdef".repeat(4);
  await page.route("**/api/devices", (route) => route.fulfill({
    json: [{ id: deviceId, name: "Living room speaker", state: "connected" }],
  }));
  await page.setViewportSize({ width: 390, height: 844 });
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await openSettings(page,"Access & security");
  const panel = page.locator(".auth-panel").filter({
    has: page.getByRole("heading", { name: "Access & security", exact: true }),
  });
  await expect(panel.getByText(deviceId, { exact: false })).toBeVisible();
  await expect(panel.getByRole("button", { name: "recover device", exact: true })).toBeVisible();
  for (const region of [page.getByRole("main"), panel]) {
    expect(await region.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  }
});

test("Settings manages real webhook configuration without voice", async ({
  page,
  server,
}) => {
  await login(page, server);
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await openSettings(page,"Webhooks");
  await page.getByRole("button", { name: "Add webhook" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Acceptance action");
  await page
    .getByLabel("URL", { exact: true })
    .fill("http://127.0.0.1:9/action");
  await page.getByLabel("JSON body").fill('{"action":"test"}');
  await page.getByRole("button", { name: "Create", exact: true }).click();
  const row = page
    .locator("main li")
    .filter({ hasText: "Acceptance action" })
    .first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Duplicate", exact: true }).click();
  await expect(page.locator("main li")).toHaveCount(2);
  await row.getByRole("button", { name: "Edit", exact: true }).click();
  const editor = page
    .locator("main li")
    .filter({ has: page.getByRole("button", { name: "Save", exact: true }) });
  await expect(editor.getByRole("textbox").first()).toHaveValue(
    "Acceptance action",
  );
  await editor.getByRole("textbox").first().fill("Renamed action");
  await editor.getByRole("button", { name: "Save", exact: true }).click();
  const renamed = page.locator("main li").filter({ hasText: "Renamed action" });
  await expect(renamed).toBeVisible();
  await renamed.getByRole("button", { name: "Delete", exact: true }).click();
  await renamed.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.locator("main li")).toHaveCount(1);
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await openSettings(page,"Webhooks");
  await expect(page.locator("main li")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Connect browser", exact: true }),
  ).toBeVisible();
});

test("agent names persist independently of shared routing and credentials", async ({
  page,
  context,
  server,
}) => {
  test.setTimeout(60000);
  await login(page, server);
  const { request, secret } = await requestIntegration(
    server,
    "Household helper",
  );
  const enrollment = page.getByRole("region", {
    name: "Integration enrollment",
    exact: true,
  });
  await openIntegrations(page);
  await enrollment.getByRole("button", { name: "Refresh integration requests", exact: true }).click();
  const requestRow = enrollment.getByRole("region", {
    name: `Integration request ${request.request_id}`,
    exact: true,
  });
  await requestRow
    .getByLabel("Integration matching code")
    .fill(request.user_code);
  await requestRow.getByRole("checkbox").check();
  await requestRow.getByText("Approve matching integration").click();
  const delivery = await client(server, "deliver", secret);
  save(server, delivery.credential);
  await client(server, "ack", {
    ...secret,
    credential: delivery.credential,
    saved: true,
  });
  await openIntegrations(page);
  await enrollment.getByRole("button", { name: "Refresh integration requests", exact: true }).click();
  await closeAgentDetail(page);
  await page.getByRole("button", {name:"Agents",exact:true}).click();
  await page.getByRole("button", {name: "Refresh agents", exact: true}).click();
  page.on("dialog", (dialog) => dialog.accept());
  await page
    .locator("main li")
    .filter({ hasText: "Household helper" })
    .getByText("Set as default", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "View agent Household helper" })
    .click();
  const detail = page.locator("dialog.agent-detail-modal");
  await detail.getByRole("button", {name:"Edit agent name"}).click();
  await detail.getByLabel("Agent name").fill("Kitchen guide");
  await detail.getByRole("button", { name: "Save name" }).click();
  await expect(detail.getByRole("status").filter({ hasText: "Agent name saved." })).toHaveText("Agent name saved.");
  await expect(page.locator(".companion-header h2")).toHaveText(
    "Kitchen guide",
  );
  const endpoint = `${server.origin}/api/agents/${request.agent_id}`;
  expect(
    (
      await context.request.patch(endpoint, {
        data: { name: "Denied" },
        headers: { Origin: server.origin },
      })
    ).status(),
  ).toBe(403);
  expect(
    (await native(server, "/api/agents", undefined, delivery.credential))
      .status,
  ).toBe(403);
  const device = await pairDevice(server, delivery.credential);
  const speaker = await socket(server, "/ws", {
    type: "hello",
    device_id: device.device_id,
    token: device.device_token,
  });
  try {
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Expand voice companion" }).click();
    await screenshot(page, "populated-agent-detail");
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Devices", exact: true }).click();
    await page.getByText(device.device_id, { exact: true }).first().click();
    await page.getByLabel("Name", { exact: true }).fill("Kitchen speaker");
    await page.getByLabel("Name", { exact: true }).blur();
    await expect(page.locator(".context-chip")).toHaveText(
      "Viewing Kitchen speaker",
    );
    for (const theme of ["light", "dark"]) {
      await page.getByLabel("Appearance").selectOption(theme);
      await screenshot(page, `populated-device-${theme}-desktop`);
      await page.keyboard.press("Escape");
      await page.setViewportSize({ width: 390, height: 844 });
      await screenshot(page, `populated-device-${theme}-phone`);
      await closeAgentDetail(page);
      await page
        .getByRole("button", { name: "Expand voice companion" })
        .click();
      await screenshot(page, `populated-device-${theme}-phone-voice`);
      await page.keyboard.press("Escape");
      await page.setViewportSize({ width: 1366, height: 900 });
      await closeAgentDetail(page);
      await page
        .getByRole("button", { name: "Expand voice companion" })
        .click();
    }
    // A remote owner update is observed without a local activation event.
    const session = await (
      await context.request.get(server.origin + "/api/auth/session")
    ).json();
    const renamed = await context.request.patch(endpoint, {
      data: { name: "Remote guide" },
      headers: { Origin: server.origin, "X-CSRF-Token": session.csrf_token },
    });
    expect(renamed.status()).toBe(200);
    await expect(page.locator(".companion-header h2")).toHaveText(
      "Remote guide",
      { timeout: 8000 },
    );
    const agents = await (
      await context.request.get(server.origin + "/api/agents")
    ).json();
    expect(
      agents.find((agent: any) => agent.id === request.agent_id).active,
    ).toBe(true);
    await server.restart();
    await page.reload();
    await expect(page.getByRole("main")).toBeVisible();
    await page.getByRole("button", { name: "View agent Remote guide" }).click();
    await expect(detail.getByRole("heading", {name:"Remote guide"})).toBeVisible();
  } finally {
    speaker.ws.close();
  }
});

test("collapsed voice dock reports pending transport and explicit cancellation", async ({
  page,
  server,
}) => {
  let connection: any;
  await page.routeWebSocket(/\/ws$/, (socket) => {
    connection = socket;
    socket.onMessage(() => {});
  });
  await login(page, server);
  await closeAgentDetail(page);
  await page
    .getByRole("button", { name: "Connect browser", exact: true })
    .click();
  await expect(page.locator(".companion-header [role=status]")).toHaveText(
    "Connecting · Mic off",
  );
  await closeAgentDetail(page);
  await page.getByRole("button", { name: "Collapse voice companion" }).click();
  await expect(
    page.getByRole("button", { name: "Cancel connection" }),
  ).toBeVisible();
  connection.send(JSON.stringify({ type: "hello" }));
  await expect(page.locator(".companion-header [role=status]")).toHaveText(
    "Ready · Mic off",
  );
  await expect(
    page.getByRole("button", { name: "Hold to Talk" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Connect browser", exact: true }),
  ).toBeVisible();
});
