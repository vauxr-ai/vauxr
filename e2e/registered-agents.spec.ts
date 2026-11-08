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
      .getByLabel("Registered implementation")
      .selectOption("household");
    await create.getByLabel("Agent name").fill("Household assistant");
    await create
      .getByRole("button", { name: "Create agent", exact: true })
      .click();
    const detail = page.getByRole("region", {
      name: "Agent detail",
      exact: true,
    });
    await expect(
      detail.getByRole("heading", { name: "Household assistant", exact: true }),
    ).toBeVisible();
    await expect(
      detail.getByRole("region", { name: "Agent model runtime", exact: true }),
    ).toContainText("Setup needed");
    await expect(
      detail.getByLabel("Enable Live", { exact: true }),
    ).toBeDisabled();
    console.log(
      `Registered agent ${width}: Standard enabled, Live unavailable`,
    );
    await page
      .getByRole("button", { name: "Connect browser", exact: true })
      .click();
    console.log(`Registered agent ${width}: browser connected`);
    const companion = page.locator("#voice-companion");
    await companion
      .getByLabel("Conversation agent", { exact: true })
      .selectOption({ label: "Household assistant" });
    console.log(`Registered agent ${width}: agent selected`);
    await companion
      .getByLabel("Text message", { exact: true })
      .fill("Help with the household");
    await companion
      .getByRole("button", { name: "Send text", exact: true })
      .click();
    console.log(`Registered agent ${width}: text sent`);
    await expect(
      companion.getByLabel("Agent text response", { exact: true }),
    ).toContainText("needs setup");
    await companion
      .getByRole("button", { name: "Collapse voice companion", exact: true })
      .click();
    await detail
      .getByRole("button", { name: "Remove agent", exact: true })
      .click();
    await expect(page.getByRole("alert")).toContainText("unassign all devices");
    await companion
      .getByRole("button", { name: "Expand voice companion", exact: true })
      .click();
    await companion
      .getByLabel("Conversation agent", { exact: true })
      .selectOption("vauxr-support");
    await companion
      .getByRole("button", { name: "Collapse voice companion", exact: true })
      .click();
    await detail
      .getByRole("button", { name: "Remove agent", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "View agent Household assistant",
        exact: true,
      }),
    ).toHaveCount(0);
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
