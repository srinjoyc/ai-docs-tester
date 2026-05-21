import { defineConfig } from "@playwright/test";

const port = process.env.PLAYWRIGHT_PORT || "3000";
const baseURL = `http://localhost:${port}`;

export default defineConfig({
  testDir: process.env.GRADER_DIR || __dirname,
  testMatch: "e2e.test.ts",
  // One test, no parallelism needed
  workers: 1,
  // Give the full test up to 45s (dev server start + wallet connect + tx)
  timeout: 45_000,
  use: {
    baseURL,
    headless: true,
    // Don't record videos/traces by default — keeps grader output lean
    video: "off",
    trace: "off",
  },
  webServer: {
    command: `npm run dev -- --port ${port}`,
    url: baseURL,
    // Work dir is passed via env var so the config is reusable
    cwd: process.env.WORK_DIR || process.cwd(),
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: "ignore",
    stderr: "ignore",
  },
});
