import { defineConfig, devices } from "@playwright/test";

// Owner setup renders one-time secrets; suppress automatic failure DOM snapshots too.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

const baseURL = process.env.VAUXR_URL ?? "http://localhost:8080";

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
