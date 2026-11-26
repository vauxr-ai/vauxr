import { mkdirSync } from "node:fs";
import { test, expect, login, openSettings } from "./auth-fixture";

for (const width of [375, 390, 1366]) for (const theme of ["light", "dark"])
  test(`telemetry default, payload and opt-out ${width}/${theme}`, async ({ page, server }) => {
    await page.setViewportSize({ width, height: width === 1366 ? 768 : 844 });
    await login(page, server);
    await page.getByLabel("Appearance").selectOption(theme);
    await openSettings(page, "Privacy & telemetry");
    const lastSection = page.getByRole("navigation", { name: "Settings sections" }).getByRole("button").last();
    await expect(lastSection).toContainText("Privacy & telemetry");
    const summary = "Help improve Vauxr with anonymous usage statistics";
    const settings = page.locator(".settings-layout");
    const panel = settings.locator(".telemetry-settings");
    await expect(lastSection.getByText(summary, { exact: true })).toHaveCount(0);
    const panelSummary = panel.getByText(summary, { exact: true });
    await expect(settings.getByText(summary, { exact: true }).filter({ visible: true })).toHaveCount(1);
    const exclusions = panel.getByText(
      "No conversations, audio, prompts, responses, errors, logs, secrets, hostnames, URLs, device or user names, or location are sent.",
      { exact: true },
    );
    for (const notice of [panelSummary, exclusions]) {
      await expect(notice).toBeVisible();
      await expect(notice).toBeInViewport({ ratio: 1 });
      expect(await notice.evaluate((element) => {
        const { left, top, width, height } = element.getBoundingClientRect();
        return element.contains(document.elementFromPoint(left + width / 2, top + height - 1));
      })).toBe(true);
    }
    await expect(panel.getByText(/hourly usage totals|Reports contain a random monthly identifier|AWS sees/)).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Privacy & telemetry settings" })).toBeFocused();
    await expect(page.getByText("Exact serialized payload · queued", { exact: true })).toBeVisible();
    await expect(page.locator(".telemetry-payload")).toBeHidden();
    mkdirSync("/tmp/vauxr-126-expanded-privacy", { recursive: true });
    await page.screenshot({ path: `/tmp/vauxr-126-expanded-privacy/${width}-${theme}-default.png` });
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
