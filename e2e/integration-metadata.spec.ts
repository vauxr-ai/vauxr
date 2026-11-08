import {openAgentAction} from "./auth-fixture";
import { closeAgentDetail } from "./auth-fixture";
import { writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, login } from "./auth-fixture";

for (const width of [375, 390, 1366]) for (const theme of ["light", "dark"]) {
  test(`registry configuration provenance ${width} ${theme}`, async ({ page, server }) => {
    writeFileSync(resolve(server.data, "agents.json"), JSON.stringify({ version: 1, active_agent: "fixture-route", agents: [
      { id: "fixture-route", name: "Household integration", type: "openclaw", createdAt: "2026-11-08" },
    ] }));
    await server.restart();
    let sockets = 0;
    page.on("websocket", () => sockets++);
    await page.addInitScript(() => {
      Object.defineProperty(window, "micCalls", { value: 0, writable: true });
      navigator.mediaDevices.getUserMedia = async () => {
        (window as unknown as { micCalls: number }).micCalls++;
        throw new DOMException("Microphone disabled for evidence", "NotAllowedError");
      };
    });
    await login(page, server);
    await page.setViewportSize({ width, height: 844 });
    await page.getByLabel("Appearance").selectOption(theme);
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await expect(page.getByRole("heading",{name:"Household integration", level:3, exact:true})).toBeVisible();
    await expect(page.locator("dialog")).toHaveCount(0);
    await openAgentAction(page, "Runtime settings for Household integration");
    const diagnostics = page.getByRole("dialog", { name: "Agent runtime settings", exact: true });
    await expect(diagnostics).toHaveClass(/agent-diagnostics-modal/);
    const metadata = diagnostics.getByRole("region", { name: "Integration configuration" });
    await expect(metadata.getByText(/Registered route/)).toBeVisible();
    await expect(metadata.getByText(/does not prove connectivity/)).toBeVisible();
    await expect(metadata.getByRole("button")).toHaveCount(0);
    await expect(page.locator("dialog.agent-detail-modal")).toContainText("Default");
    await metadata.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(sockets).toBe(0);
    expect(await page.evaluate(() => (window as unknown as { micCalls: number }).micCalls)).toBe(0);
    const artifacts = process.env.VAUXR_METADATA_ARTIFACT_DIR;
    if (artifacts) {
      mkdirSync(artifacts, { recursive: true });
      await page.screenshot({ path: resolve(artifacts, `integration-metadata-${width}-${theme}.png`) });
    }
    await closeAgentDetail(page);
    await page.locator('main li[data-agent-id="fixture-route"]').getByRole("button", {name:"Edit agent name"}).click();
    await page.getByLabel("Agent name", { exact: true }).fill("Renamed household");
    await page.getByRole("button", { name: "Save name", exact: true }).click();
    await expect(page.getByText("Agent name saved.")).toBeVisible();
    await page.reload();
    await closeAgentDetail(page);
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await expect(page.getByRole("region", { name: "Integration configuration" })).toHaveCount(0);
    await closeAgentDetail(page);
    await openAgentAction(page, "Runtime settings for Renamed household");
    await expect(metadata.getByText(/does not prove connectivity/)).toBeVisible();
  });
}
