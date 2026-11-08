import {openAgentAction} from "./auth-fixture";
import { closeAgentDetail } from "./auth-fixture";
import { test, expect, login } from "./auth-fixture";
for (const width of [390, 1366])
  test(`registered agent setup, modes and removal at ${width}`, async ({
    page,
    server,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => {
      (window as any).micCalls = 0;
      navigator.mediaDevices.getUserMedia = async () => {
        (window as any).micCalls++;
        throw Error("Mic off");
      };
    });
    await login(page, server);
    await page
      .getByRole("button", { name: "+ Add Agent", exact: true })
      .click();
    const create = page.getByRole("dialog", { name: "Add Agent", exact: true });
    await create
      .getByRole("button", {name:"Choose Household assistant",exact:true})
      .click();
    await create.getByLabel("Agent name").fill("Household assistant");
    await create
      .getByRole("button", { name: "Create agent", exact: true })
      .click();
    const detail = page.locator("dialog.agent-detail-modal");
    await expect(
      page.getByRole("heading", { name: "Household assistant", level:3, exact: true }),
    ).toBeVisible();
    await expect(detail.getByRole("region", {name:"Agent voice", exact:true})).toHaveCount(0);
    await closeAgentDetail(page);
    await openAgentAction(page, "Voice pipeline for Household assistant");
    await expect(detail.getByRole("tab", {name:"Live / Realtime", exact:true})).toHaveCount(0);
    await expect(detail.getByRole("region", { name: "Agent model runtime", exact: true })).toHaveCount(0);
    await expect(detail.getByRole("button", { name: "Remove agent", exact: true })).toHaveCount(0);
    await closeAgentDetail(page);
    await openAgentAction(page, "Runtime settings for Household assistant");
    const diagnostics = page.getByRole("dialog", { name: "Agent runtime settings", exact: true });
    await expect(diagnostics.getByRole("region", { name: "Agent model runtime", exact: true })).toContainText("Setup needed");
    console.log(
      `Registered agent ${width}: Standard enabled, Live unavailable`,
    );
    await closeAgentDetail(page);
    await page
      .getByRole("button", { name: "Connect browser", exact: true })
      .click();
    console.log(`Registered agent ${width}: browser connected`);
    const companion = page.locator("#voice-companion");
    await closeAgentDetail(page);
    await companion
      .getByLabel("Conversation agent", { exact: true })
      .selectOption({ label: "Household assistant" });
    console.log(`Registered agent ${width}: agent selected`);
    await closeAgentDetail(page);
    await companion
      .getByLabel("Text message", { exact: true })
      .fill("Help with the household");
    await closeAgentDetail(page);
    await companion
      .getByRole("button", { name: "Send text", exact: true })
      .click();
    console.log(`Registered agent ${width}: text sent`);
    await expect(
      companion.getByLabel("Agent text response", { exact: true }),
    ).toContainText("needs setup");
    await closeAgentDetail(page);
    await companion
      .getByRole("button", { name: "Collapse voice companion", exact: true })
      .click();
    const menu=page.locator('details.agent-actions').filter({hasText:'Voice pipeline'}).filter({has:page.getByRole('button',{name:'Runtime settings for Household assistant',includeHidden:true})});
    if(!await menu.evaluate(el=>(el as HTMLDetailsElement).open)) await menu.locator('summary').click();
    await menu.getByRole('button',{name:'Delete agent',exact:true}).click();
    const confirmation=page.getByRole('dialog',{name:'Delete agent',exact:true});
    await expect(confirmation).toContainText('Household assistant');
    await confirmation.getByRole('button',{name:'Delete agent',exact:true}).click();
    await expect(confirmation).toHaveCount(0);
    await expect(page.getByRole('heading',{name:'Household assistant', level:3, exact:true})).toHaveCount(0);
    const speech=await (await page.request.get(server.origin+'/api/speech')).json();
    expect(Object.values(speech.assignments || {})).not.toContain('Household assistant');
    console.log(
      `Registered agent ${width}: setup-needed text and guarded removal verified`,
    );
    expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
    await page.screenshot({
      timeout: 10000,
      animations: "disabled",
      path: `/tmp/vauxr-registered-agent-${width}.png`,
      fullPage: true,
    });
  });
