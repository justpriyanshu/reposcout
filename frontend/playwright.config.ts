import { defineConfig, devices } from "@playwright/test";

// E2E tests run against the production build served by `vite preview` (pages at /static/…), with
// every API call mocked in the browser (e2e/mock.ts): no RepoScout server is needed or touched.
const PORT = 4173;
const preview = `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`;

export default defineConfig({
  testDir: "e2e",
  outputDir: ".tmp/test-results",
  // One worker: the dev machines and CI runners this targets are small.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  timeout: 30_000,
  expect: { timeout: 7_000 },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    colorScheme: "light",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    // Set E2E_SKIP_BUILD=1 when `npm run build` has just run.
    command: process.env.E2E_SKIP_BUILD ? preview : `npm run build && ${preview}`,
    url: `http://127.0.0.1:${PORT}/static/app.html`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
