import { defineConfig } from "@playwright/test";

const port = process.env.PLAYWRIGHT_PORT || "3000";
const baseURL = `http://localhost:${port}`;
const realMode = Boolean(
  process.env.TEST_WALLET_PRIVATE_KEY && process.env.ZERODEV_PROJECT_ID
);

export default defineConfig({
  testDir: process.env.GRADER_DIR || __dirname,
  testMatch: "e2e.test.ts",
  workers: 1,
  // Real-mode includes on-chain polling (up to ~90s); mock mode is fast
  timeout: realMode ? 180_000 : 60_000,
  use: {
    baseURL,
    headless: true,
    video: "off",
    trace: "off",
  },
  webServer: {
    // e2e.sh starts Next.js manually and waits for it — we just reuse it here.
    command: `echo "server already started by e2e.sh"`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 5_000,
  },
});
