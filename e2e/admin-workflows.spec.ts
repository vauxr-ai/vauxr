import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, login } from "./auth-fixture";

for (const width of [375, 390, 1366]) for (const theme of ["light", "dark"]) {
  test(`administration without voice at ${width} ${theme}`, async ({ page, server }) => {
    const artifacts = process.env.VAUXR_ADMIN_ARTIFACT_DIR;
    const capture = async (state: string) => {
      if (!artifacts) return;
      mkdirSync(artifacts, { recursive: true });
      await page.screenshot({ path: resolve(artifacts, `admin-${width}-${theme}-${state}.png`) });
    };
    const provider = "household_transcription_provider_with_a_long_name";
    const synthesis = "household_synthesis_provider_with_a_long_name";
    const voice = "english_household_voice_with_a_long_populated_name";
    writeFileSync(resolve(server.data, "speech.json"), JSON.stringify({ version: 1,
      providers: [
        { id: provider, kind: "stt", adapter: "whisper", model: "multilingual_household_model", host: "127.0.0.1", port: 1 },
        { id: synthesis, kind: "tts", adapter: "piper", model: "household_synthesis_model", host: "127.0.0.1", port: 1, voices: [voice] },
      ], defaults: { mode: "standard", stt_backend: provider, tts_backend: synthesis, voices: { [synthesis]: voice } }, devices: {} }));
    await server.restart();
    await page.addInitScript(() => {
      (window as any).adminMicCalls = 0;
      navigator.mediaDevices.getUserMedia = async () => { (window as any).adminMicCalls++; throw Error("Administration must not capture"); };
    });
    let sockets = 0;
    page.on("websocket", () => sockets++);
    await login(page, server);
    await page.setViewportSize({ width, height: 844 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.getByLabel("Appearance").selectOption(theme);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByText("No webhooks yet.")).toBeVisible();
    const sections = page.getByRole("navigation", { name: "Settings sections" });
    await sections.getByRole("link", { name: "Voice providers/defaults" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("#voice-defaults")).toBeFocused();
    await expect(page.getByLabel("STT backend", { exact: true })).toHaveValue(provider);
    for (const select of await page.locator(".administration select").all()) {
      if (!await select.isVisible()) continue;
      expect(await select.evaluate(element => {
        const card = element.closest(".card")!;
        const b = card.getBoundingClientRect(), s = getComputedStyle(card), r = element.getBoundingClientRect();
        return r.left >= b.left + parseFloat(s.borderLeftWidth) + parseFloat(s.paddingLeft) - 1 && r.right <= b.right - parseFloat(s.borderRightWidth) - parseFloat(s.paddingRight) + 1;
      })).toBe(true);
    }
    await capture("providers");
    await sections.getByRole("link", { name: "Webhooks", exact: true }).click();
    const hooks = page.getByRole("region", { name: "Webhooks", exact: true });
    await hooks.getByRole("button", { name: "Add webhook" }).click();
    await hooks.getByLabel("Name", { exact: true }).fill("Disposable household action with a long populated name");
    await hooks.getByLabel("URL", { exact: true }).fill("https://fixture.invalid/household/action/long-path");
    await hooks.getByLabel("JSON body").fill('{"entity_id":"scene.disposable_fixture"}');
    await expect(hooks.getByText(/Unsaved webhook edits/)).toBeVisible();
    page.once("dialog", dialog => dialog.dismiss());
    await page.getByRole("button", { name: "Agents", exact: true }).click();
    await expect(hooks.getByLabel("Name", { exact: true })).toHaveValue("Disposable household action with a long populated name");
    const create = hooks.getByRole("button", { name: "Create", exact: true });
    for (const control of await hooks.locator("input, textarea").all()) {
      const box = (await control.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
    }
    await page.locator("main").evaluate(element => { element.scrollTop = element.scrollHeight; });
    const b = (await create.boundingBox())!;
    const main = (await page.locator("main").boundingBox())!;
    expect(b.y + b.height).toBeLessThanOrEqual(main.y + main.height);
    expect(await create.evaluate(element => { const b = element.getBoundingClientRect(); return element.contains(document.elementFromPoint(b.x+b.width/2, b.y+b.height/2)); })).toBe(true);
    await capture("draft");
    await create.focus();
    await page.keyboard.press("Enter");
    await expect(hooks.getByText("Webhook change saved.")).toBeVisible();
    await hooks.getByRole("button", { name: "Edit", exact: true }).click();
    await expect(hooks.getByLabel("Webhook URL")).toHaveValue("https://fixture.invalid/household/action/long-path");
    await hooks.getByLabel("Webhook name").fill("Edited disposable action");
    await hooks.getByRole("button", { name: "Save", exact: true }).click();
    await expect(hooks.getByText("Edited disposable action", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => (window as any).adminMicCalls)).toBe(0);
    expect(sockets).toBe(0);
    await expect(page.getByRole("button", { name: "Connect browser", exact: true })).toBeVisible();
    await capture("saved");
  });
}
