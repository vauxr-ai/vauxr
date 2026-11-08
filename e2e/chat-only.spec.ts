import { closeAgentDetail } from "./auth-fixture";
import { openIntegrations } from "./auth-fixture";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, login, requestIntegration, client, socket } from "./auth-fixture";

const artifacts = process.env.VAUXR_COMPANION_ARTIFACT_DIR;
for (const width of [375, 390, 1366]) for (const theme of ["light", "dark"]) {
  test(`chat-only persists and browser sends real text without voice ${width} ${theme}`, async ({ page, server }) => {
    test.setTimeout(60000);
    await page.setViewportSize({ width, height: 844 });
    await page.addInitScript(() => {
      (window as any).micCalls = 0;
      navigator.mediaDevices.getUserMedia = async () => {
        (window as any).micCalls++;
        throw new Error("Chat-only must never request a microphone");
      };
    });
    const operations: string[] = [];
    page.on("websocket", ws => ws.on("framesent", frame => {
      if (typeof frame.payload === "string") operations.push(JSON.parse(frame.payload).type);
    }));
    await login(page, server);
    await page.getByLabel("Appearance").selectOption(theme);
    const { request, secret } = await requestIntegration(server, "Household text companion");
    const enrollment = page.getByRole("region", { name: "Integration enrollment", exact: true });
    await openIntegrations(page);
    await enrollment.getByRole("button", { name: "Refresh integration requests", exact: true }).click();
    const row = enrollment.getByRole("region", { name: `Integration request ${request.request_id}`, exact: true });
    await row.getByLabel("Integration matching code").fill(request.user_code);
    await row.getByRole("checkbox").check();
    await row.getByText("Approve matching integration").click();
    const delivery = await client(server, "deliver", secret);
    await client(server, "ack", { ...secret, credential: delivery.credential, saved: true });
    const agent = await socket(server, "/agent", { type: "agent.auth", token: delivery.credential });
    try {
      await closeAgentDetail(page);
      await page.getByRole("button", {name:"Agents",exact:true}).click();
      await page.getByRole("button", {name: "Refresh agents", exact: true}).click();
      await page.getByRole("button", { name: "View agent Household text companion" }).click();
      const voice = page.getByRole("region", { name: "Agent voice", exact: true });
      await voice.getByLabel("Enable Standard").uncheck();
      await voice.getByLabel("Enable Live").uncheck();
      for (const name of ["Default Standard", "Default Live"]) await expect(voice.getByRole("switch", {name, exact:true})).not.toBeChecked();
      await voice.getByRole("button", { name: "Save voice configuration" }).click();
      await expect(voice.getByText(/Chat-only · No voice modes enabled/)).toBeVisible();
      await closeAgentDetail(page);
      await page.getByRole("button", { name: "Connect browser", exact: true }).click();
      const companion = page.locator("#voice-companion");
      await expect(companion.getByLabel("Conversation agent")).toBeEnabled();
      await closeAgentDetail(page);
      await companion.getByLabel("Conversation agent").selectOption(`int_${request.request_id}`);
      await expect(companion.getByLabel("Text message", { exact: true })).toBeEnabled();
      await expect(companion.getByRole("button", { name: /Hold to Talk|Start listening/i })).toHaveCount(0);
      await expect(companion.getByRole("slider", { name: "Output volume" })).toHaveCount(0);
      await expect(companion.getByText("Chat-only · Voice unavailable", { exact: true })).toBeVisible();
      await closeAgentDetail(page);
      await companion.getByLabel("Text message").fill("Explain the selected display name");
      await closeAgentDetail(page);
      await companion.getByRole("button", { name: "Send text", exact: true }).click();
      await expect.poll(() => agent.messages.some(frame => frame.type === "agent.transcript")).toBe(true);
      const frame = agent.messages.find(frame => frame.type === "agent.transcript")!;
      expect(frame.text).toContain("Explain the selected display name");
      expect(frame.text).toContain("Untrusted browser UI data");
      expect(frame.text).not.toContain(delivery.credential);
      agent.ws.send(JSON.stringify({ type: "agent.response.delta", deviceId: frame.deviceId, runId: "text-run", text: "The display name identifies this agent. This is a text-only response." }));
      agent.ws.send(JSON.stringify({ type: "agent.response.end", deviceId: frame.deviceId, runId: "text-run" }));
      await expect(companion.getByLabel("Agent text response")).toContainText("text-only response");
      expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
      expect(operations.filter(type => ["voice.start", "voice.end", "realtime.start"].includes(type))).toEqual([]);
      if (artifacts) {
        mkdirSync(artifacts, { recursive: true });
        await closeAgentDetail(page);
        await companion.getByLabel("Agent text response").scrollIntoViewIfNeeded();
        await page.screenshot({ path: resolve(artifacts, `chat-only-${width}-${theme}-text-response.png`) });
      }
      await page.keyboard.press("Escape");
      await server.restart();
      await page.reload();
      await page.getByRole("button", { name: "View agent Household text companion" }).click();
      await expect(page.getByLabel("Enable Standard")).not.toBeChecked();
      await expect(page.getByLabel("Enable Live")).not.toBeChecked();
      await closeAgentDetail(page);
      await page.getByRole("button", { name: "Connect browser", exact: true }).click();
      await expect(companion.getByLabel("Conversation agent")).toHaveValue(`int_${request.request_id}`);
      await expect(companion.getByText("Chat-only · Voice unavailable", { exact: true })).toBeVisible();
      expect(await page.evaluate(() => (window as any).micCalls)).toBe(0);
      expect(operations.filter(type => ["voice.start", "voice.end", "realtime.start"].includes(type))).toEqual([]);
    } finally { agent.ws.close(); }
  });
}
