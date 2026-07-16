// Playwright config for the browser (T044, US3/FR-033) test suite.
//
// Location choice: this file lives at the REPO ROOT (not `frontend/`)
// because the task wording puts spec files at `tests/e2e/` — a
// repo-root-relative path, matching the Python `tests/` tree's sibling
// directories (`tests/unit`, `tests/integration`) rather than a
// frontend-only concern. `testDir` below points at that directory. The
// frontend package.json gets a matching `test:e2e` script
// (`playwright test --config ../playwright.config.ts` run from `frontend/`,
// or simply `playwright test` from the repo root) once `@playwright/test`
// is installed — see the frontend `devDependencies` entry added alongside
// this file.
//
// NOT RUN in this sandbox: `npm`/`playwright` are not installed here (no
// network access), so these specs are written-but-unexecuted, same as the
// T040-era ghostty-web integration code. They are structured to run for
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
        cwd: "./frontend",
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
