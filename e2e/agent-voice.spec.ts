import { closeAgentDetail } from "./auth-fixture";
import { openIntegrations } from "./auth-fixture";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";
import {
  test,
  expect,
  login,
  requestIntegration,
  client,
  pairDevice,
  socket,
} from "./auth-fixture";

const artifacts = process.env.VAUXR_VOICE_ARTIFACT_DIR;
const stt =
  "household_multilingual_transcription_provider_with_long_identifier";
const tts = "household_synthesis_provider_with_long_identifier";
const voice = "household_model_scoped_voice_with_long_identifier";
const live = "household_live_provider_with_long_identifier";

async function capture(page: Page, name: string, card: Locator) {
  if (!artifacts) return;
  mkdirSync(artifacts, { recursive: true });
  await card.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: resolve(artifacts, `${name}.png`) });
  await card.screenshot({ path: resolve(artifacts, `${name}-card.png`) });
}
async function bounds(card: Locator) {
  const result = await card.evaluate((element) => {
    const box = element.getBoundingClientRect(),
      style = getComputedStyle(element);
    return {
      left:
        box.left +
        parseFloat(style.borderLeftWidth) +
        parseFloat(style.paddingLeft),
      right:
        box.right -
        parseFloat(style.borderRightWidth) -
        parseFloat(style.paddingRight),
      selects: [...element.querySelectorAll("select")].map((select) => {
        const rect = select.getBoundingClientRect();
        return { left: rect.left, right: rect.right };
      }),
    };
  });
  expect(result.selects.length).toBeGreaterThan(1);
  for (const select of result.selects) {
    expect(select.left).toBeGreaterThanOrEqual(result.left - 1);
    expect(select.right).toBeLessThanOrEqual(result.right + 1);
  }
}
async function reloadSession(page: Page) {
  await page.reload();
  await expect(page.getByRole("main")).toBeVisible();
}

for (const width of [375, 390])
  for (const theme of ["light", "dark"]) {
    test(`agent voice inheritance, conflicts, restart and responsive controls ${width} ${theme}`, async ({
      page,
      server,
      context,
    }) => {
      test.setTimeout(90000);
      writeFileSync(
        resolve(server.data, "speech.json"),
        JSON.stringify({
          version: 1,
          providers: [
            {
              id: stt,
              kind: "stt",
              adapter: "whisper",
              model: "long_multilingual_model_identifier",
              host: "127.0.0.1",
              port: 1,
            },
            {
              id: tts,
              kind: "tts",
              adapter: "piper",
              model: "long_household_synthesis_model_identifier",
              host: "127.0.0.1",
              port: 1,
              voices: [voice, "alternate_voice"],
            },
            {
              id: live,
              kind: "realtime",
              adapter: "openai-live",
              model: "gpt-realtime",
              host: "api.openai.com",
              port: 443,
              voices: ["ash", "coral"],
            },
          ],
          defaults: {
            mode: "standard",
            stt_backend: stt,
            tts_backend: tts,
            voices: { [tts]: voice },
            realtime_backend: live,
            realtime_voice: "ash",
          },
          devices: {},
        }),
      );
      await server.restart();
      await page.addInitScript(() => {
        (window as any).micCalls = 0;
        navigator.mediaDevices.getUserMedia = async () => {
          (window as any).micCalls++;
          throw new Error("No microphone requested by management tests");
        };
      });
      await login(page, server);
      await page.getByLabel("Appearance").selectOption(theme);
      await page.emulateMedia({reducedMotion:"reduce"});
      const { request, secret } = await requestIntegration(
        server,
        "Household agent with a long populated display name",
      );
      const enrollment = page.getByRole("region", {
        name: "Integration enrollment",
        exact: true,
      });
      await openIntegrations(page);
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
      const device = await pairDevice(server, delivery.credential);
      let speaker = await socket(server, "/ws", {
        type: "hello",
        device_id: device.device_id,
        token: device.device_token,
        caps: ["ws", "webrtc"],
      });
      try {
        await closeAgentDetail(page);
        await page.getByRole("button", {name:"Agents",exact:true}).click();
        await page.getByRole("button", {name: "Refresh agents", exact: true}).click();
        await page
          .getByRole("button", {
            name: "Voice pipeline for Household agent with a long populated display name",
          })
          .click();
        const agent = page.getByRole("region", {
          name: "Agent voice",
          exact: true,
        });
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        await expect(agent.getByLabel("Enable Live")).toBeChecked();
        await agent
          .getByLabel("Voice", { exact: true })
          .selectOption("alternate_voice");
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        await expect(agent.getByLabel("Voice", {exact:true})).toHaveValue("alternate_voice");
        await expect.poll(async () => {
          const response = await page.request.get(`${server.origin}/api/agents/${request.agent_id}/speech`);
          return (await response.json()).effective;
        }).toMatchObject({stt_backend: stt, tts_backend: tts, voice_id: "alternate_voice"});
        await page.emulateMedia({ reducedMotion: "reduce" });

        await page.setViewportSize({ width, height: 844 });
        await bounds(agent);
        await capture(page, `${width}-${theme}-agent-standard`, agent);
        await agent
          .getByRole("tab", {name:"Live / Realtime",exact:true}).click();
        await agent.getByLabel("Realtime voice", { exact: true }).selectOption("coral");
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        await expect(agent.getByLabel("Realtime voice", { exact: true })).toHaveValue("coral");
        await bounds(agent);
        await capture(page, `${width}-${theme}-agent-live-providers`, agent);
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Devices", exact: true })
          .click();
        await page.getByText(device.device_id, { exact: true }).first().click();
        const card = page.getByRole("region", {
          name: "Device speech",
          exact: true,
        });
        await card
          .getByLabel("Assigned agent", { exact: true })
          .selectOption(request.agent_id);
        await card
          .getByRole("button", { name: "Save agent assignment" })
          .click();
        await expect(
          card.getByText("Assigned agent. Inherited voice defaults.", {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          card.getByText(`Effective: ${stt} / ${tts} / alternate_voice`, {
            exact: true,
          }),
        ).toBeVisible();
        await bounds(card);
        await capture(page, `${width}-${theme}-device-inherited`, card);
        await card
          .getByLabel("Voice mode", { exact: true })
          .selectOption("realtime");
        await card.getByLabel("Realtime voice", { exact: true }).selectOption("ash");
        await expect(card.getByLabel("Realtime voice", { exact: true })).toHaveValue("ash");
        await bounds(card);
        await capture(page, `${width}-${theme}-device-overridden-live`, card);
        await closeAgentDetail(page);
        await page.getByRole("button", { name: "Agents", exact: true }).click();
        await page
          .getByRole("button", {
            name: "Voice pipeline for Household agent with a long populated display name",
          })
          .click();
        await agent.getByLabel("Enable Live").uncheck();
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        await expect(agent.getByRole("alert")).toContainText(device.device_id);
        await capture(page, `${width}-${theme}-conflicting-override`, agent);
        // Failed writes persist nothing; a deliberate reset resolves the named override.
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Devices", exact: true })
          .click();
        await page.getByText(device.device_id, { exact: true }).first().click();
        await card.getByRole("button", { name: "Reset to defaults" }).click();
        await expect(
          card.getByLabel("Voice mode", { exact: true }),
        ).toHaveValue("");
        await card
          .getByLabel("Voice mode", { exact: true })
          .selectOption("realtime");
        await expect(card.getByLabel("Realtime voice", { exact: true })).toHaveValue("");
        await expect(
          card.getByLabel("Realtime voice", { exact: true }),
        ).toHaveAccessibleDescription("Selected: Inherit agent (coral)");
        await card.getByRole("button", { name: "Reset to defaults" }).click();
        await closeAgentDetail(page);
        await page.getByRole("button", { name: "Agents", exact: true }).click();
        await page
          .getByRole("button", {
            name: "Voice pipeline for Household agent with a long populated display name",
          })
          .click();
        await agent.getByLabel("Enable Live").uncheck();
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        await expect(agent.getByLabel("Enable Live")).not.toBeChecked();
        await page.reload();
        await page
          .getByRole("button", {
            name: "Voice pipeline for Household agent with a long populated display name",
          })
          .click();
        await expect(agent.getByLabel("Enable Live")).not.toBeChecked();
        // Capability loss is learned on authenticated reconnect and cannot silently switch modes.
        await agent.getByLabel("Enable Live").check();
        await agent.getByLabel("Enable Standard").uncheck();
        await agent.getByRole("switch", {name:"Default Live",exact:true}).check();
        await agent
          .getByRole("tab", {name:"Live / Realtime",exact:true}).click();
        await agent.getByLabel("Realtime voice", { exact: true }).selectOption("ash");
        const atomicSave = page.waitForResponse(
          (response) =>
            response.url().endsWith(`/api/agents/${request.agent_id}/speech`) &&
            response.request().method() === "PATCH",
        );
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        const applied = await atomicSave;
        expect(applied.status()).toBe(200);
        expect(applied.request().postDataJSON()).toEqual({
          enabled_modes: ["realtime"],
          default_mode: "realtime",
          realtime_voice: "ash",
        });
        await expect(agent.getByLabel("Enable Standard")).not.toBeChecked();
        await capture(page, `${width}-${theme}-agent-live-only`, agent);
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Devices", exact: true })
          .click();
        await page.getByText(device.device_id, { exact: true }).first().click();
        await expect(
          card
            .getByLabel("Voice mode", { exact: true })
            .getByRole("option", { name: "Standard", exact: true }),
        ).toHaveCount(0);
        await expect(
          card.getByLabel("Voice mode", { exact: true }),
        ).toHaveValue("");
        await expect(
          card.getByLabel("Realtime voice", { exact: true }),
        ).toHaveAccessibleDescription("Selected: Inherit agent (ash)");
        await closeAgentDetail(page);
        await page.getByRole("button", { name: "Agents", exact: true }).click();
        await page
          .getByRole("button", {
            name: "Voice pipeline for Household agent with a long populated display name",
          })
          .click();
        await agent.getByLabel("Enable Standard").check();
        await agent.getByRole("switch", {name:"Default Standard",exact:true}).check();
        await agent
          .getByRole("button", { name: "Save voice configuration" })
          .click();
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Devices", exact: true })
          .click();
        await page.getByText(device.device_id, { exact: true }).first().click();
        await card
          .getByLabel("Voice mode", { exact: true })
          .selectOption("realtime");
        speaker.ws.close();
        speaker = await socket(server, "/ws", {
          type: "hello",
          device_id: device.device_id,
          token: device.device_token,
          caps: ["ws"],
        });
        await expect
          .poll(() =>
            speaker.messages.some(
              (message) => message.code === "VOICE_CONFLICT",
            ),
          )
          .toBe(true);
        await card.getByRole("button", { name: "Refresh speech" }).click();
        await expect(card.getByRole("alert")).toContainText("unavailable");
        await capture(page, `${width}-${theme}-capability-conflict`, card);
        await card.getByRole("button", { name: "Reset to defaults" }).click();
        await expect(card.getByRole("alert")).toHaveCount(0);
        await server.restart();
        await reloadSession(page);
        speaker = await socket(server, "/ws", {
          type: "hello",
          device_id: device.device_id,
          token: device.device_token,
          caps: ["ws"],
        });
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Devices", exact: true })
          .click();
        await page.getByText(device.device_id, { exact: true }).first().click();
        await expect(
          card.getByLabel("Assigned agent", { exact: true }),
        ).toHaveValue(request.agent_id);
        await expect(
          card.getByLabel("Voice mode", { exact: true }),
        ).toHaveValue("");
        await expect(
          card.getByText(`Effective: ${stt} / ${tts} / alternate_voice`, {
            exact: true,
          }),
        ).toBeVisible();
        const session = await (
          await context.request.get(server.origin + "/api/auth/session")
        ).json();
        const denied = await context.request.patch(
          `${server.origin}/api/devices/${device.device_id}/speech`,
          {
            headers: {
              Origin: server.origin,
              "X-CSRF-Token": session.csrf_token,
            },
            data: { mode: "realtime" },
          },
        );
        expect(denied.status()).toBe(409);
        await page.setViewportSize({ width: 1366, height: 900 });
        await bounds(card);
        await capture(page, `${width}-${theme}-desktop-reloaded`, card);
        await closeAgentDetail(page);
        await page
          .getByRole("button", { name: "Expand voice companion" })
          .click();
        await bounds(card);
        await capture(page, `${width}-${theme}-desktop-expanded`, card);
        expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
      } finally {
        speaker.ws.close();
      }
    });
  }
