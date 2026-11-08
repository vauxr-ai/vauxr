import { createHash } from "node:crypto";
import { test, expect, login } from "./auth-fixture";
for (const width of [390, 1366]) test(`implemented plugin package download at ${width}`, async ({page, server}) => {
  await page.setViewportSize({width, height:900});
  await login(page, server);
  await page.getByRole("button", {name:"Settings",exact:true}).click();
  const plugins = page.getByRole("region",{name:"Integration plugins",exact:true});
  await expect(plugins.getByRole("link",{name:"Download OpenClaw plugin",exact:true})).toBeVisible();
  const response = await page.request.get(server.origin+"/api/plugins");
  expect(response.status()).toBe(200);
  const entry = (await response.json()).find((p:any) => p.id === "openclaw");
  const archive = await page.request.get(server.origin+entry.download);
  expect(archive.status()).toBe(200);
  expect(createHash("sha256").update(await archive.body()).digest("hex")).toBe(entry.sha256);
  await expect(plugins).toContainText("Pipecat integration plugin is not available");
  await plugins.scrollIntoViewIfNeeded();
  await page.screenshot({path:`/tmp/vauxr-plugins-${width}.png`,fullPage:true});
});
