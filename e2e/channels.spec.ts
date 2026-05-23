/**
 * UI smoke test for the channels page.
 *
 * Drives the web client through the channel-create + rotate-token flow.
 * Designed to catch regressions where channel writes 500 — e.g. the
 * docker /data permission mismatch that the Alpine→slim base swap
 * introduced (see Dockerfile uid pinning).
 */
import { expect, test, type Page } from "@playwright/test";

const DEVICE_TOKEN = process.env.VAUXR_DEVICE_TOKEN ?? "anything-you-want";

async function connect(page: Page): Promise<void> {
  await page.goto("/");
  await page.locator('input[type="password"]').fill(DEVICE_TOKEN);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText("Online", { exact: true })).toBeVisible({
    timeout: 10_000,
  });
}

async function gotoChannels(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Channels" }).click();
  await expect(
    page.getByRole("heading", { name: "Channels", exact: true }),
  ).toBeVisible();
}

async function createChannel(page: Page, name: string): Promise<string> {
  await page.getByRole("button", { name: /Add channel/ }).click();
  await page.getByPlaceholder(/Home OpenClaw/).fill(name);
  await page.getByRole("button", { name: "Create", exact: true }).click();

  // The TokenModal shows label='Token for "<name>"' and the token text in
  // a div with the `font-mono` class — that's the most stable selector.
  await expect(page.getByText(`Token for "${name}"`)).toBeVisible({
    timeout: 10_000,
  });
  const token = (await page.locator("div.font-mono").first().textContent()) ?? "";
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.getByText(name, { exact: true })).toBeVisible();
  return token.trim();
}

test("create channel via UI does not 500", async ({ page }) => {
  // Catches: POST /api/channels → 500 (e.g. /data unwritable in container).
  // The UI surfaces server errors as a red banner above the channel list;
  // assert there is none and the channel actually appears.
  await connect(page);
  await gotoChannels(page);

  const name = `e2e-create-${Date.now()}`;
  const token = await createChannel(page, name);

  expect(token).toMatch(/^vx_ch_[0-9a-f]{64}$/);
  // No error banner.
  await expect(page.locator("p.border-red-500\\/30")).toHaveCount(0);
});

test("rotate channel token via UI returns a new token", async ({ page }) => {
  // Catches: POST /api/channels/{id}/rotate → 500. Same root cause as
  // create, but exercised after a successful create so we cover the full
  // round-trip (load → mutate → save).
  await connect(page);
  await gotoChannels(page);

  const name = `e2e-rotate-${Date.now()}`;
  const firstToken = await createChannel(page, name);

  const row = page.locator("li", { hasText: name }).first();
  await row.getByRole("button", { name: /Rotate token/ }).click();
  await page.getByRole("button", { name: "Rotate", exact: true }).click();

  await expect(page.getByText("New token (rotated)")).toBeVisible({
    timeout: 10_000,
  });
  const newToken =
    (await page.locator("div.font-mono").first().textContent()) ?? "";
  expect(newToken.trim()).toMatch(/^vx_ch_[0-9a-f]{64}$/);
  expect(newToken.trim()).not.toBe(firstToken);

  await page.getByRole("button", { name: "Done", exact: true }).click();
});
