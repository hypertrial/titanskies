import { defineConfig, devices } from "@playwright/test";

const e2eDistDir = ".next-e2e";
const e2ePort = Number(process.env.TITANSKIES_E2E_PORT ?? "3010");
if (!Number.isInteger(e2ePort) || e2ePort < 1 || e2ePort > 65535) {
  throw new Error("TITANSKIES_E2E_PORT must be an integer from 1 to 65535");
}
const e2eBaseUrl = `http://127.0.0.1:${e2ePort}`;
process.env.TITANSKIES_E2E_DIST_DIR = e2eDistDir;

export default defineConfig({
  testDir: "tests/e2e",
  globalSetup: "./tests/e2e/global-setup.ts",
  globalTeardown: "./tests/e2e/global-teardown.ts",
  // Dev-mode WebGL uses SwiftShader in the release gate. Run one browser page
  // at a time so frame and pointer assertions are deterministic; performance
  // budgets live in the production benchmark.
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: e2eBaseUrl,
    trace: "on-first-retry",
    timezoneId: "UTC",
  },
  webServer: {
    command: `exec ./node_modules/.bin/next dev --port ${e2ePort} --hostname 127.0.0.1`,
    env: {
      NEXT_DIST_DIR: e2eDistDir,
      NEXT_PUBLIC_CONTEXT_URL: "/demo/context/latest.json",
    },
    // Static demo JSON is served as soon as Next is listening. Waiting on `/`
    // compiles the globe page (three + r3f + drei) and can exceed 2 minutes.
    url: `${e2eBaseUrl}/demo/context/latest.json`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /mobile-webkit\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-webkit",
      testMatch: /mobile-webkit\.spec\.ts/,
      use: { ...devices["iPhone 13"] },
    },
  ],
});
