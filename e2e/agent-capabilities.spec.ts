import { writeFileSync, mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, login } from "./auth-fixture";

for (const width of [375, 390, 1366]) for (const theme of ["light", "dark"]) {
  test(`honest capability boundaries ${width} ${theme}`, async ({ page, server }) => {
    writeFileSync(resolve(server.data, "agents.json"), JSON.stringify({ version: 1, active_agent: "fixture-route", agents: [
      { id: "fixture-route", name: "Household integration", type: "openclaw", createdAt: "2026-11-08" },
    ] }));
    await server.restart();
    await login(page, server);
    await page.setViewportSize({ width, height: 844 });
    await page.getByLabel("Appearance").selectOption(theme);
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await page.getByRole("button", { name: "View agent Household integration" }).click();
    const capabilities = page.getByRole("region", { name: "Integration capabilities" });
    await expect(capabilities.getByText(/Registered route/)).toBeVisible();
    await expect(capabilities.getByText(/transport is not implemented/)).toHaveCount(2);
    await expect(capabilities.getByRole("button")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Active", exact: true })).toBeDisabled();
    await capabilities.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const artifacts = process.env.VAUXR_CAPABILITY_ARTIFACT_DIR;
    if (artifacts) {
      mkdirSync(artifacts, { recursive: true });
      await page.screenshot({ path: resolve(artifacts, `capabilities-${width}-${theme}.png`) });
    }
    await page.getByLabel("Agent name", { exact: true }).fill("Renamed household");
    await page.getByRole("button", { name: "Save name", exact: true }).click();
    await expect(page.getByText("Agent name saved.")).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await page.getByRole("button", { name: "View agent Renamed household" }).click();
    await expect(capabilities.getByText(/transport is not implemented/)).toHaveCount(2);
  });
}
