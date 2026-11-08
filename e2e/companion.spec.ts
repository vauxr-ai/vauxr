import { openIntegrations, closeAgentDetail } from "./auth-fixture";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, login, requestIntegration, client } from "./auth-fixture";

const artifacts = process.env.VAUXR_COMPANION_ARTIFACT_DIR;
for (const width of [375, 390, 1366]) for (const theme of ["light", "dark"]) {
  test(`direct companion settings ${width} ${theme}`, async ({ page, server }) => {
    test.setTimeout(60000);
    await page.setViewportSize({ width, height: 844 });
    await page.addInitScript(() => {
      (window as any).micCalls = 0;
      navigator.mediaDevices.getUserMedia = async () => {
        (window as any).micCalls++;
        throw new DOMException("Fixture microphone denied", "NotAllowedError");
      };
    });
    await login(page, server);
    await page.getByLabel("Appearance").selectOption(theme);
    const { request, secret } = await requestIntegration(server, "Household companion with a long visible name");
    const enrollment = page.getByRole("region", { name: "Integration enrollment", exact: true });
    await openIntegrations(page);
    await enrollment.getByRole("button", { name: "Refresh integration requests", exact: true }).click();
    const row = enrollment.getByRole("region", { name: `Integration request ${request.request_id}`, exact: true });
    await row.getByLabel("Integration matching code").fill(request.user_code);
    await row.getByRole("checkbox").check();
    await row.getByText("Approve matching integration").click();
    const delivery = await client(server, "deliver", secret);
    await client(server, "ack", { ...secret, credential: delivery.credential, saved: true });
    await page.getByRole("button", {name:"Agents",exact:true}).click();
    await page.getByRole("button", {name: "Refresh agents", exact: true}).click();
    let sockets = 0;
    page.on("websocket", () => sockets++);
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Connect browser", exact: true }).click();
    const companion = page.locator("#voice-companion");
    await expect(companion.getByLabel("Conversation agent")).toBeEnabled();
    await companion.getByLabel("Conversation agent").selectOption(`int_${request.request_id}`);
    await companion.getByRole("button", { name: "Collapse voice companion", exact: true }).click();
    const detail = page.locator(`main li[data-agent-id="${request.agent_id}"]`);
    await detail.getByRole("button", { name: "Edit agent name", exact: true }).click();
    await detail.getByLabel("Agent name", { exact: true }).fill("Kitchen helper");
    const geometry = await detail.evaluate(element => {
      const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
      return { left: rect.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft),
        right: rect.right - parseFloat(style.borderRightWidth) - parseFloat(style.paddingRight),
        fields: [...element.querySelectorAll("select, input")].map(field => {
          const box = field.getBoundingClientRect(); return { left: box.left, right: box.right };
        }) };
    });
    for (const field of geometry.fields) {
      expect(field.left).toBeGreaterThanOrEqual(geometry.left - 1);
      expect(field.right).toBeLessThanOrEqual(geometry.right + 1);
    }
    const button = detail.getByRole("button", { name: "Save name", exact: true });
    await button.scrollIntoViewIfNeeded();
    expect(await button.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    })).toBe(true);
    if (artifacts) {
      mkdirSync(artifacts, { recursive: true });
      await page.screenshot({ path: resolve(artifacts, `companion-${width}-${theme}-edit.png`) });
    }
    await button.click();
    await expect(detail.getByRole("heading", { name: "Kitchen helper", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Devices", exact: true }).click();
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    expect(sockets).toBe(1);
    expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
    if (artifacts) await page.screenshot({ path: resolve(artifacts, `companion-${width}-${theme}-saved.png`) });
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Expand voice companion", exact: true }).click();
    await expect(companion.getByLabel("Conversation agent")).toHaveValue(`int_${request.request_id}`);
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Connect browser", exact: true }).click();
    await expect.poll(() => sockets).toBe(2);
    expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
  });
}
