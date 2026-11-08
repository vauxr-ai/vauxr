import { mkdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  test,
  expect,
  login,
  requestIntegration,
  client,
} from "./auth-fixture";

test.use({ actionTimeout: 10000 });

const artifacts = "/tmp/vauxr-b2-settings-screenshots";
for (const width of [320, 390, 1440])
  test(`persistent B2 settings, mic off, ${width}`, async ({
    page,
    server,
  }) => {
    test.setTimeout(90000);
    mkdirSync(artifacts, { recursive: true });
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(() => {
      (window as any).micCalls = 0;
      navigator.mediaDevices.getUserMedia = async () => {
        (window as any).micCalls++;
        throw Error("Mic remains off");
      };
    });
    await login(page, server);
    await page.getByLabel("Appearance").selectOption("dark");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const nav = page.getByRole("navigation", { name: "Settings sections" });
    const select = async (name: string) => {
      await nav.getByRole("button", { name: new RegExp(`^${name}`) }).click();
    };
    const capture = async (name: string) => {
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      for (const field of await page
        .locator(
          '.settings-content input:not([type="checkbox"]), .settings-content select, .settings-content textarea',
        )
        .all()) {
        if (!(await field.isVisible())) continue;
        const rect = await field.boundingBox();
        expect(rect!.x).toBeGreaterThanOrEqual(0);
        expect(rect!.x + rect!.width).toBeLessThanOrEqual(width);
      }
      await page.screenshot({
        path: resolve(artifacts, `${width}-${name}.png`),
      });
    };
    await expect(
      page.getByRole("heading", { name: "Speech to text", exact: true }),
    ).toBeVisible();
    await capture("loading");
    await expect(page.getByLabel("Default deployment",{exact:true})).toBeVisible();
    await page.mouse.move(width - 5, 5);
    await capture("overview");
    await page.getByRole("button", { name: /whisper.*legacy-whisper/ }).click();
    await page
      .getByRole("heading", { name: "whisper", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("stt-editor");
    await page.getByLabel("Model", { exact: true }).fill("edited-whisper");
    page.once("dialog", (d) => d.dismiss());
    await select("Text to speech");
    await expect(page.getByText("Unsaved voice provider edits")).toBeVisible();
    await page
      .getByRole("button", { name: "Save voice provider", exact: true })
      .click();
    await expect(page.getByText(/Voice provider saved/)).toBeVisible();
    const speech = JSON.parse(
      readFileSync(resolve(server.data, "speech.json"), "utf8"),
    );
    expect(speech.providers.find((b: any) => b.id === "whisper").model).toBe(
      "edited-whisper",
    );
    await select("Text to speech");
    await page
      .getByRole("button", { name: /openai-tts.*gpt-4o-mini-tts/ })
      .click();
    await page
      .getByRole("heading", { name: "openai-tts", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("tts-editor");
    await select("Realtime");
    await page.getByRole("button", { name: /openai-live.*gpt-live-1/ }).click();
    await page
      .getByRole("heading", { name: "openai-live", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("realtime-editor");
    await select("Model providers");
    await expect(
      page.getByText("No saved profiles", { exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "+ Add provider profile", exact: true })
      .click();
    await page.getByLabel("Profile ID", { exact: true }).fill("test-local");
    await page
      .getByLabel("Display name", { exact: true })
      .fill("Household model service");
    await page
      .getByLabel("Endpoint", { exact: true })
      .fill("http://ollama.local:11434");
    await page.getByLabel("Model ID", { exact: true }).fill("household-model");
    await page
      .getByRole("button", { name: "Save provider profile", exact: true })
      .click();
    await expect(page.getByText(/Provider profile saved/)).toBeVisible();
    await page.getByRole("button", { name: /Household model service/ }).click();
    await page
      .getByRole("heading", { name: "Edit provider profile", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("model-profile");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page
      .getByRole("button", { name: "+ Add provider profile", exact: true })
      .click();
    await page.getByLabel("Provider", { exact: true }).selectOption("openai");
    await page.getByLabel("Profile ID", { exact: true }).fill("shared-openai");
    await page
      .getByLabel("Display name", { exact: true })
      .fill("Shared OpenAI credential");
    await page.getByLabel("Model ID", { exact: true }).fill("configured-model");
    await page
      .getByLabel("Authentication", { exact: true })
      .selectOption("inline");
    await page
      .getByLabel("Replace credential", { exact: true })
      .fill("fixture-secret-never-rendered");
    await page
      .getByRole("button", { name: "Save provider profile", exact: true })
      .click();
    await expect(page.getByText(/Provider profile saved/)).toBeVisible();
    expect(
      readFileSync(resolve(server.data, "provider-settings.json"), "utf8"),
    ).not.toContain("fixture-secret-never-rendered");
    await page
      .getByRole("button", { name: /Shared OpenAI credential/ })
      .click();
    await expect(
      page.getByLabel("Replace credential", { exact: true }),
    ).toHaveValue("");
    await expect(page.locator("body")).not.toContainText(
      "fixture-secret-never-rendered",
    );
    await page
      .getByRole("heading", { name: "Edit provider profile", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("credential-masked");
    await page
      .getByLabel("Replace credential", { exact: true })
      .scrollIntoViewIfNeeded();
    await capture("credential-input-empty");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await select("Plugins");
    await expect(
      page.getByRole("heading", { name: "No plugin settings registered" }),
    ).toBeVisible();
    await page
      .getByRole("heading", { name: "Plugin settings", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("plugins-empty");
    await select("Apps & Integrations");
    await expect(
      page.getByRole("heading", { name: "Connect an agent", exact: true }),
    ).toBeVisible();
    const { request, secret } = await requestIntegration(
      server,
      "Disposable external agent",
    );
    const enrollment = page.getByRole("region", {
      name: "Integration enrollment",
      exact: true,
    });
    await enrollment.getByRole("button", { name: "Refresh integration requests", exact: true }).click();
    const row = enrollment.getByRole("region", {
      name: `Integration request ${request.request_id}`,
      exact: true,
    });
    await row.getByLabel("Integration matching code").fill(request.user_code);
    await row.getByRole("checkbox").check();
    await row.getByText("Approve matching integration").click();
    const delivery = await client(server, "deliver", secret);
    await client(server, "ack", {
      ...secret,
      credential: delivery.credential,
      saved: true,
    });
    await page
      .getByRole("heading", { name: "Connect an agent", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("apps-integrations");
    await select("Interfaces");
    await expect(page.getByLabel("HTML source", { exact: true })).toBeVisible();
    await page
      .getByRole("heading", { name: "Interfaces", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("interfaces");
    await select("Webhooks");
    await expect(page.getByText("No webhooks yet.")).toBeVisible();
    await page
      .getByRole("heading", { name: "Webhooks", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("webhooks");
    await select("Access & security");
    await expect(
      page.getByRole("heading", { name: "Access & security", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Spoken four-digit code")).toHaveCount(0);
    await page
      .getByRole("heading", { name: "Access & security", exact: true })
      .scrollIntoViewIfNeeded();
    await capture("access-security");
    await page.getByRole("button", { name: "Devices", exact: true }).click();
    await page
      .getByRole("button", { name: "+ Add Device", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Add Device", exact: true }),
    ).toBeVisible();
    await capture("add-device");
    await page
      .getByRole("button", { name: /Expand (voice|text) companion/ })
      .click();
    await page
      .getByText("Browser session diagnostics", { exact: true })
      .click();
    await expect(
      page.locator("#voice-companion details.diagnostics"),
    ).toHaveAttribute("open", "");
    await capture("companion-diagnostics");
    await page.keyboard.press("Escape");
    await server.restart();
    await page.reload();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await select("Model providers");
    await expect(
      page.getByRole("button", { name: /Household model service/ }),
    ).toBeVisible();
    expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
  });
