// Playwright config for the browser (T044, US3/FR-033) test suite.
//
// Location choice: this config and the specs it runs both live under
// `frontend/`, because both import `@playwright/test` and the only install of
// it is `frontend/node_modules`.
//
// They used to live at the repo root (`playwright.config.ts`, `tests/e2e/`),
// as siblings of the Python `tests/unit` and `tests/integration` trees. That
// reads nicely but could never run: Node resolves a bare import by walking UP
// from the importing file, so from the repo root `frontend/node_modules` is
// *below* the search path and never consulted. `npm run test:e2e` died with
// "Cannot find module '@playwright/test'" — first on this config, then on the
// specs themselves. Co-locating both with the dependency is what makes the
// script actually executable.
//
// NOT RUN in this sandbox: `npm`/`playwright` are not installed here (no
// network access), so these specs are written-but-unexecuted. They are
// structured to run for
// real against a `vite dev` server (frontend) proxying to a real
// `remo web serve` backend with disposable SSH fixtures (mirroring
// `tests/integration/test_nine_terminals.py`'s 3x3 fixture) in CI/local dev
// once dependencies are installable.
//
// Backend dependency: nearly every spec here needs a real discovery
// snapshot (SessionTargets) to open terminals against, which requires a
// running `remo web serve` pointed at a real-or-disposable registry. Rather
// than mock that at the network layer (which would drift from the real
// contracts/websocket protocol and give false confidence), each spec calls
// `test.skip(...)` up front when `REMO_E2E_BACKEND_URL` isn't set, so the
// suite is honest about needing that fixture rather than silently no-op
// passing.

import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.REMO_E2E_BASE_URL ?? "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-safari",
      // FR-033: basic mobile keyboard/input operation, on an emulated
      // touch/mobile viewport per Playwright's built-in device profile.
      use: { ...devices["iPhone 13"] },
    },
  ],
  // Only start a local dev server automatically when no external base URL
  // was supplied — CI or a developer may point REMO_E2E_BASE_URL at an
  // already-running Docker Compose stack (docker/compose.example.yml)
  // instead.
  webServer: process.env.REMO_E2E_BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        cwd: ".",
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
