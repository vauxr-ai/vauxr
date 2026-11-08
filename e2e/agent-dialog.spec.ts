import { mkdirSync } from "node:fs";
import { test, expect, login } from "./auth-fixture";
for (const width of [390, 1366])
  for (const theme of ["light", "dark"]) {
    test(`agent dialog keyboard and creation at ${width} ${theme}`, async ({
      page,
      server,
    }) => {
      await page.setViewportSize({ width, height: 844 });
      await login(page, server);
      await page.getByLabel("Appearance").selectOption(theme);
      await page.getByRole("button", { name: "Agents", exact: true }).click();
      const trigger = page.getByRole("button", {
        name: "+ Add Agent",
        exact: true,
      });
      await trigger.click();
      const modal = page.getByRole("dialog", {
        name: "Add Agent",
        exact: true,
      });
      await expect(modal).toBeVisible();
      expect(await modal.evaluate((el) => el.matches(":modal"))).toBe(true);
      const bounds = (await modal.boundingBox())!;
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await modal
        .getByLabel("Agent name", { exact: true })
        .fill("Design fixture");
      for (let n = 0; n < 10; n++) {
        await page.keyboard.press("Tab");
        expect(
          await modal.evaluate((el) => el.contains(document.activeElement)),
        ).toBe(true);
      }
      mkdirSync("/tmp/vauxr-polish-screenshots", { recursive: true });
      await page.screenshot({
        path: `/tmp/vauxr-polish-screenshots/dialog-${width}-${theme}.png`,
      });
      await page.keyboard.press("Escape");
      await expect(modal).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await trigger.click();
      await modal
        .getByRole("button", { name: "Create agent", exact: true })
        .click();
      await expect(modal).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await page.reload();
      await expect(
        page.getByRole("button", {
          name: "View agent Design fixture",
          exact: true,
        }),
      ).toBeVisible();
      await page.screenshot({
        path: `/tmp/vauxr-polish-screenshots/agents-${width}-${theme}.png`,
      });
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await expect(
        page.getByText("Loading voice providers…", { exact: true }),
      ).toHaveCount(0);
      await page.screenshot({
        path: `/tmp/vauxr-polish-screenshots/settings-${width}-${theme}.png`,
      });
    });
  }
