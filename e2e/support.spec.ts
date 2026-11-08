import { writeFileSync, readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
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


test("pipeline model radio selection persists through the authenticated runtime API and restart", async ({page, server}) => {
  writeFileSync(resolve(server.data, "provider-settings.json"), JSON.stringify({version:1, speech_auth:{}, profiles:[
    {id:"pipeline-local", name:"Pipeline local model", provider:"ollama", endpoint:"http://127.0.0.1:1", model:"fixture-model", auth:"none"},
  ]}));
  await login(page, server);
  await page.getByRole("button", {name:"Voice pipeline for Vauxr Support",exact:true}).click();
  await page.getByRole("button", {name:"Edit Vauxr Support",exact:true}).click();
  const entity = page.getByRole("dialog", {name:"Vauxr Support settings",exact:true});
  await entity.getByRole("radio", {name:/Pipeline local model/}).check();
  expect(existsSync(resolve(server.data,"local-agent-models.json")) ? readFileSync(resolve(server.data,"local-agent-models.json"),"utf8") : "").not.toContain("pipeline-local");
  await page.keyboard.press("Escape");
  const runtimeWrite = page.waitForResponse(response => response.url().endsWith("/api/local-agents/vauxr-support/runtime") && response.request().method()==="POST");
  await page.getByRole("button", {name:"Save pipeline configuration",exact:true}).click();
  expect((await runtimeWrite).status()).toBe(200);
  await expect(page.getByText(/Pipeline configuration saved/)).toBeVisible();
  expect(JSON.parse(readFileSync(resolve(server.data,"local-agent-models.json"),"utf8"))["vauxr-support"]).toEqual({profile_id:"pipeline-local"});
  await server.restart(); await page.reload();
  await page.getByRole("button", {name:"Voice pipeline for Vauxr Support",exact:true}).click();
  await page.getByRole("button", {name:"Edit Vauxr Support",exact:true}).click();
  await expect(entity.getByRole("radio", {name:/Pipeline local model/})).toBeChecked();
});
