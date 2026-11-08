import { test, expect, login, closeAgentDetail } from "./auth-fixture";
for (const width of [390, 1366]) test(`built-in Support is independent and honest without runtime at ${width}`, async ({ page, server }) => {
  await page.setViewportSize({width, height: 900});
  await page.addInitScript(() => {
    (window as any).micCalls = 0;
    navigator.mediaDevices.getUserMedia = async () => { (window as any).micCalls++; throw Error("Mic must stay off"); };
  });
  await login(page, server);
  await page.getByRole("button", {name:"Runtime settings for Vauxr Support", exact:true}).click();
  const diagnostics = page.getByRole("dialog", {name:"Agent runtime settings", exact:true});
  const setup = diagnostics.getByRole("region", {name:/^(Support runtime|Agent model runtime)$/, exact:true});
  await expect(setup).toContainText("Setup needed");
  await expect(setup.getByRole("combobox")).toHaveValue("");
  await closeAgentDetail(page);
  await page.getByRole("button", {name:"Connect browser", exact:true}).click();
  const companion = page.locator("#voice-companion");
  await expect(companion.getByLabel("Conversation agent")).toBeEnabled();
  await companion.getByLabel("Conversation agent").selectOption("vauxr-support");
  await companion.getByLabel("Text message").fill("Explain setup");
  await companion.getByRole("button", {name:"Send text", exact:true}).click();
  await expect(companion.getByLabel("Agent text response")).toContainText("needs setup");
  expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
  await page.screenshot({path:`/tmp/vauxr-support-${width}.png`, fullPage:true});
  await server.restart();
  await page.reload();
  await expect(page.getByRole("button", {name:"View agent Vauxr Support",exact:true})).toBeVisible();
});
