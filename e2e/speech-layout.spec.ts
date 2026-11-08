import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import { test, expect, login } from "./auth-fixture";

const stt = "whisper_household_multilingual_transcription_provider";
const tts = "piper_household_multilingual_synthesis_provider";
const voice = "en_US_household_voice_with_a_long_model_scoped_identifier";
const realtime = "realtime_household_provider_with_a_long_identifier";
const liveVoice = "household_realtime_voice_with_a_long_selected_identifier";
const artifacts = process.env.VAUXR_B2_ARTIFACT_DIR;

async function capture(page: Page, name: string, card?: Locator) {
  if (!artifacts) return;
  mkdirSync(artifacts, { recursive: true });
  const path = resolve(artifacts, `${name}.png`);
  if (card)
    await card.evaluate((element) =>
      element.scrollIntoView({ block: "start" }),
    );
  await page.screenshot({ path });
}

async function expectInsideSpeechCard(card: Locator) {
  const geometry = await card.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const left =
      bounds.left +
      parseFloat(style.borderLeftWidth) +
      parseFloat(style.paddingLeft);
    const right =
      bounds.right -
      parseFloat(style.borderRightWidth) -
      parseFloat(style.paddingRight);
    return {
      left,
      right,
      children: [
        ...element.querySelectorAll<HTMLElement>(
          "fieldset, label, select, button, .speech-selection",
        ),
      ].map((control) => {
        const box = control.getBoundingClientRect();
        return {
          name:
            control.getAttribute("aria-label") || control.textContent?.trim(),
          left: box.left,
          right: box.right,
        };
      }),
    };
  });
  expect(geometry.children.length).toBeGreaterThan(5);
  for (const control of geometry.children) {
    expect(
      control.left,
      `${control.name}: full left edge inside card padding`,
    ).toBeGreaterThanOrEqual(geometry.left - 1);
    expect(
      control.right,
      `${control.name}: full right edge / native arrow inside card padding`,
    ).toBeLessThanOrEqual(geometry.right + 1);
  }
  for (const select of await card.getByRole("combobox").all()) {
    if (await select.getAttribute("aria-describedby")) {
      const label = await select.locator("option:checked").textContent();
      await expect(select).toHaveAccessibleDescription(`Selected: ${label}`);
      const description = card.locator(
        `[id="${await select.getAttribute("aria-describedby")}"]`,
      );
      expect(
        await description.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      ).toBe(true);
    }
  }
}

async function expectAboveDock(control: Locator, page: Page) {
  // Test the real scroll limit, not a document overflow flag that can hide clipping.
  await page.locator("main").evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  const box = (await control.boundingBox())!;
  const dock = (await page.locator(".companion.is-collapsed").boundingBox())!;
  const main = (await page.locator("main").boundingBox())!;
  expect(box.y).toBeGreaterThanOrEqual(main.y);
  expect(box.y + box.height).toBeLessThan(dock.y - 8);
  expect(
    await control.evaluate((element) => {
      const box = element.getBoundingClientRect();
      const points = [
        [box.left + 2, box.top + 2],
        [box.right - 2, box.bottom - 2],
        [box.left + box.width / 2, box.top + box.height / 2],
      ];
      return points.every(([x, y]) =>
        element.contains(document.elementFromPoint(x, y)),
      );
    }),
  ).toBe(true);
}

for (const width of [375, 390]) {
  for (const theme of ["light", "dark"]) {
    test(`populated speech controls fit inner cards and clear the dock at ${width}px ${theme}`, async ({
      page,
      server,
    }) => {
      test.setTimeout(60000);
      // Synthetic server-owned deployments only; no inference, live keys, or paid API calls.
      writeFileSync(
        resolve(server.data, "speech.json"),
        JSON.stringify({
          version: 1,
          providers: [
            {
              id: stt,
              kind: "stt",
              adapter: "whisper",
              model:
                "multilingual_transcription_model_with_a_long_populated_name",
              host: "127.0.0.1",
              port: 1,
            },
            {
              id: tts,
              kind: "tts",
              adapter: "piper",
              model: "household_synthesis_model_with_a_long_populated_name",
              host: "127.0.0.1",
              port: 1,
              voices: [voice, "alternate_voice"],
            },
            {
              id: realtime,
              kind: "realtime",
              adapter: "openai-live",
              model: "realtime_model_with_a_long_populated_name",
              host: "api.openai.com",
              port: 443,
              voices: [liveVoice],
            },
          ],
          defaults: {
            mode: "standard",
            stt_backend: stt,
            tts_backend: tts,
            voices: { [tts]: voice },
            realtime_backend: realtime,
            realtime_voice: liveVoice,
          },
          devices: {},
        }),
      );
      await server.restart();
      await page.addInitScript(() => {
        (window as any).layoutMicCalls = 0;
        navigator.mediaDevices.getUserMedia = async () => {
          (window as any).layoutMicCalls++;
          throw new Error("No capture requested by layout checks");
        };
      });
      await login(page, server);
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.getByLabel("Appearance").selectOption(theme);
      await page.setViewportSize({ width, height: 844 });
      await page
        .getByRole("button", { name: "Connect browser", exact: true })
        .click();
      await expect(
        page.getByRole("radio", { name: "Standard push-to-talk" }),
      ).toBeEnabled();
      await page
        .getByRole("button", { name: "Collapse voice companion" })
        .click();
      let sockets = 0;
      page.on("websocket", () => sockets++);
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      const global = page.getByRole("region", {
        name: "Global speech",
        exact: true,
      });
      await expect(
        global.getByRole("combobox", { name: "Voice", exact: true }),
      ).toHaveValue(voice);
      await expectInsideSpeechCard(global);
      await capture(page, `${width}-${theme}-global-standard`, global);
      await global
        .locator(".speech-field")
        .last()
        .evaluate((element) => element.scrollIntoView({ block: "center" }));
      await capture(page, `${width}-${theme}-global-selected-voice`);
      await global
        .getByLabel("Voice mode", { exact: true })
        .selectOption("realtime");
      await expect(global.getByLabel("Realtime voice")).toHaveValue(liveVoice);
      await expectInsideSpeechCard(global);
      await capture(page, `${width}-${theme}-global-realtime`, global);
      await global
        .getByLabel("Voice mode", { exact: true })
        .selectOption("standard");
      await expect(global.getByLabel("TTS backend")).toBeEnabled();
      await page.getByRole("button", { name: "Devices", exact: true }).click();
      await page.locator("main li").first().getByRole("button").first().click();
      const device = page.getByRole("region", {
        name: "Device speech",
        exact: true,
      });
      await expect(device.getByLabel("TTS backend")).toHaveValue("");
      await expectInsideSpeechCard(device);
      await capture(page, `${width}-${theme}-device-inherited`, device);
      await device
        .locator(".speech-field")
        .last()
        .evaluate((element) => element.scrollIntoView({ block: "center" }));
      await capture(page, `${width}-${theme}-device-inherited-voice`);
      await device.getByLabel("STT backend").selectOption(stt);
      await device.getByLabel("TTS backend").selectOption(tts);
      await device
        .getByRole("combobox", { name: "Voice", exact: true })
        .selectOption(voice);
      await expect(
        device.getByRole("button", { name: "Refresh speech" }),
      ).toBeEnabled();
      await expectInsideSpeechCard(device);
      await capture(page, `${width}-${theme}-device-overridden`, device);
      await device
        .getByLabel("Voice mode", { exact: true })
        .selectOption("realtime");
      await expect(device.getByLabel("Realtime voice")).toHaveValue("");
      await expect(
        device.getByLabel("Realtime voice"),
      ).toHaveAccessibleDescription(`Selected: Inherit agent (${liveVoice})`);
      await expectInsideSpeechCard(device);
      await capture(page, `${width}-${theme}-device-realtime`, device);
      await device
        .getByLabel("Voice mode", { exact: true })
        .selectOption("standard");
      await expect(device.getByLabel("TTS backend")).toBeEnabled();
      // The last real device form must be usable at maximum scroll with the connected dock.
      const controls = page.getByRole("region", {
        name: "Control",
        exact: true,
      });
      const command = controls.getByRole("combobox").first();
      const send = controls.getByRole("button", { name: "Send", exact: true });
      for (const control of [
        command,
        controls.getByLabel("Volume", { exact: true }),
        send,
      ])
        await expectAboveDock(control, page);
      await command.selectOption("set_volume");
      await controls.getByLabel("Volume", { exact: true }).fill("37");
      const sent = page.waitForResponse(
        (response) =>
          response.url().endsWith("/command") &&
          response.request().method() === "POST",
      );
      await send.click();
      const response = await sent;
      expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({
        command: "set_volume",
        params: { volume: 37 },
      });
      await capture(
        page,
        `${width}-${theme}-device-final-controls-connected-dock`,
      );
      await expect(
        page.getByRole("button", { name: "Disconnect", exact: true }),
      ).toBeVisible();
      expect(await page.evaluate(() => (window as any).layoutMicCalls)).toBe(0);
      expect(sockets).toBe(0);
      // Also verify the smaller disconnected dock, without changing form ownership.
      await page
        .getByRole("button", { name: "Disconnect", exact: true })
        .click();
      await expectAboveDock(send, page);
      await capture(page, `${width}-${theme}-device-final-controls-idle-dock`);
      // Desktop retains useful columns rather than stretching long options across the page.
      await page.setViewportSize({ width: 1366, height: 900 });
      await device.scrollIntoViewIfNeeded();
      await expectInsideSpeechCard(device);
      const columns = await device
        .locator("fieldset.speech-fields")
        .last()
        .locator(".speech-field")
        .evaluateAll((fields) =>
          fields.slice(0, 2).map((field) => ({
            top: field.getBoundingClientRect().top,
            width: field.getBoundingClientRect().width,
          })),
        );
      expect(Math.abs(columns[0].top - columns[1].top)).toBeLessThan(1);
      expect(columns[0].width).toBeGreaterThanOrEqual(200);
      await capture(page, `${width}-${theme}-desktop-device`, device);
      await page
        .getByRole("button", { name: "Expand voice companion" })
        .click();
      await expectInsideSpeechCard(device);
      const expandedColumns = await device
        .locator("fieldset.speech-fields")
        .last()
        .locator(".speech-field")
        .evaluateAll((fields) =>
          fields.slice(0, 2).map((field) => ({
            top: field.getBoundingClientRect().top,
            width: field.getBoundingClientRect().width,
          })),
        );
      expect(
        Math.abs(expandedColumns[0].top - expandedColumns[1].top),
      ).toBeLessThan(1);
      expect(expandedColumns[0].width).toBeGreaterThanOrEqual(200);
      await capture(page, `${width}-${theme}-desktop-device-expanded`, device);
      await page.getByRole("button", { name: "Settings", exact: true }).click();
      await expect(global.getByLabel("TTS backend")).toHaveValue(tts);
      await expectInsideSpeechCard(global);
      await capture(page, `${width}-${theme}-desktop-global-expanded`, global);
    });
  }
}
