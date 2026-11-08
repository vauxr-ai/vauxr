import { expect, type Locator } from "@playwright/test";

export async function pipelineMode(card: Locator, mode: string) {
  await card.getByRole("tab", {name: mode === "Live" ? "Live / Realtime" : "Standard", exact: true}).click();
}
export async function pipelineEnabled(card: Locator, mode: string, enabled: boolean) {
  await pipelineMode(card, mode);
  const button = card.getByRole("button", {name: `Enable ${mode}`, exact: true});
  if (await button.getAttribute("aria-pressed") !== String(enabled)) await button.click();
}
export async function expectPipelineEnabled(card: Locator, mode: string, enabled: boolean) {
  await pipelineMode(card, mode);
  await expect(card.getByRole("button", {name: `Enable ${mode}`, exact: true})).toHaveAttribute("aria-pressed", String(enabled));
}
export async function pipelineDefault(card: Locator, mode: string) {
  await pipelineMode(card, mode);
  await card.getByRole("button", {name: `Default ${mode}`, exact: true}).click();
}
export async function pipelineVoice(card: Locator, kind: "tts" | "realtime", voice: string, select = true) {
  await pipelineMode(card, kind === "tts" ? "Standard" : "Live");
  await card.getByRole("button", {name: kind === "tts" ? "Edit Text To Speech" : "Edit Realtime Provider", exact: true}).click();
  const popover = card.page().getByRole("dialog", {name: kind === "tts" ? "Text To Speech settings" : "Realtime Provider settings", exact: true});
  const radio = popover.getByRole("radio", {name: voice, exact: true});
  if (select) await radio.check();
  await expect(radio).toBeChecked();
  await popover.getByRole("button", {name: "Close entity settings"}).click();
}
