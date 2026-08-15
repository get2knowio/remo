// Playwright config for the browser GEOMETRY suite (tests/geometry).
//
// Separate from playwright.config.ts on purpose. That suite drives the whole
// console against a live `remo web serve` backend and skips itself without one,
// so it cannot gate CI. This one is self-contained: it serves a fixture page
// (tests/geometry/harness) with Vite and answers the terminal API and
// WebSocket from inside the spec, so it runs anywhere Chromium does and is
// therefore safe to make a required check.
//
// It exists because jsdom has no layout engine — every element measures 0x0 —
// so the Vitest suite can check the fit loop's bookkeeping but nothing about
// whether the emulator actually fits the box that clips it. See
// docs/web-session-interface.md.

import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.REMO_GEOMETRY_PORT ?? 5199);
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/geometry",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // Serial: every spec drives the shared viewport and the fixture's own state.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL,
    // A fixed, generous viewport so the first measurement is deterministic;
    // specs that care about resizing set their own.
    viewport: { width: 1400, height: 900 },
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium-webgl",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The same specs against xterm's DOM renderer. Two reasons, both real:
      // users without a usable GPU context get this renderer (XtermRenderer
      // falls back automatically), and it is the only configuration that paints
      // `.xterm-rows`, which gives the specs an INDEPENDENT read of the
      // emulator's grid. Without that, a cell height derived from the rows the
      // card reported is circular and a never-sent grid can look consistent.
      name: "chromium-dom-renderer",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: { args: ["--disable-gpu", "--disable-software-rasterizer"] },
      },
    },
  ],
  webServer: {
    // Vite rooted at the fixture; it resolves imports of ../../../src/** and
    // node_modules by walking up to frontend/.
    //
    // `--host 127.0.0.1` is load-bearing: Vite otherwise binds `localhost`
    // only, which on a dual-stack box resolves to ::1 while Playwright probes
    // the IPv4 `baseURL` — the server comes up in ~200ms and the run still dies
    // with "Timed out waiting 120000ms from config.webServer".
    command: `npx vite serve tests/geometry/harness --host 127.0.0.1 --port ${PORT} --strictPort`,
    cwd: ".",
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
