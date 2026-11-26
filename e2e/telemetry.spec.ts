import { mkdirSync } from "node:fs";
import { test, expect, login, openSettings } from "./auth-fixture";

for (const width of [390, 1366]) for (const theme of ["light", "dark"])
  test(`telemetry default, payload and opt-out ${width}/${theme}`, async ({ page, server }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 768 });
    await login(page, server);
    await page.getByLabel("Appearance").selectOption(theme);
    await openSettings(page, "Privacy & telemetry");
    const lastSection = page.getByRole("navigation", { name: "Settings sections" }).getByRole("button").last();
    await expect(lastSection).toContainText("Privacy & telemetry");
    await expect(lastSection.getByText("Help improve Vauxr with anonymous usage statistics", { exact: true })).toBeVisible();
    await expect(page.getByText(/Telemetry enabled/)).toBeVisible();
    await page.getByText("Exact serialized payload · queued", { exact: true }).click();
    const preview = page.locator(".telemetry-payload");
    const raw = await preview.textContent();
    expect(JSON.parse(raw!).kind).toBe("startup");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const box = await preview.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    mkdirSync("/tmp/vauxr-telemetry-screenshots", { recursive: true });
    await page.getByRole("heading", { name: "Usage telemetry", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/vauxr-telemetry-screenshots/${width}-${theme}-notice.png`, fullPage: true });
    await page.getByRole("button", { name: "Disable telemetry" }).click();
    await expect(page.getByText(/Telemetry disabled/)).toBeVisible();
    await page.reload();
    await openSettings(page, "Privacy & telemetry");
    await expect(page.getByText(/Telemetry disabled/)).toBeVisible();
    await page.getByRole("button", { name: "Enable telemetry" }).click();
    await expect(page.getByText(/Telemetry enabled/)).toBeVisible();
    await page.getByRole("button", { name: "Disable telemetry" }).click();
    await expect(page.getByText(/Telemetry disabled/)).toBeVisible();
  });
