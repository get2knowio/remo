// Basic mobile keyboard/input operation (FR-033): "The UI MUST remain
// functional on current desktop and tablet browsers and provide basic
// mobile keyboard/input operation." See fixtures.ts for the
// REMO_E2E_BACKEND_URL gating rationale.
//
// This spec force-uses Playwright's `iPhone 13` device profile via
// `test.use()` regardless of which project runs it, so it always exercises
// a touch/mobile viewport + user agent even if executed under the
// "desktop-chromium" project — the config's "mobile-safari" project already
// applies the same profile to every spec in the suite, but pinning it here
// too keeps this file's intent self-evident and correct if ever run in
// isolation (`playwright test mobile.spec.ts --project=desktop-chromium`).

import { devices, expect, test } from "@playwright/test";
import { TESTID, requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.use({ ...devices["iPhone 13"] });

test.describe("mobile viewport", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("dashboard renders and remains usable on a mobile viewport", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(0);

    // No horizontal overflow at a mobile viewport width — a common way a
    // desktop-first layout silently breaks on mobile.
    const hasHorizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    );
    expect(hasHorizontalOverflow).toBe(false);
  });

  test("tapping Open opens a terminal and its surface accepts touch + typed input", async ({ page }) => {
    const [targetId] = await waitForDiscoveredTargets(page);

    // `tap()` requires the mobile device profile's `hasTouch: true`
    // (provided by `devices["iPhone 13"]` above) rather than a plain click,
    // matching real mobile interaction.
    await page.getByTestId(TESTID.sessionRow(targetId)).tap();

    const card = page.getByTestId(TESTID.terminalCard(targetId));
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute("data-connection-state", "ready", { timeout: 20_000 });

    // Tap into the terminal surface (the mobile equivalent of a desktop
    // click-to-focus) — this should bring up the virtual keyboard on a real
    // device and, in-page, mark this card focused so subsequent typed input
    // routes here (same `isFocused` gating as desktop, see
    // TerminalCard.tsx).
    await page.getByTestId(TESTID.terminalSurface(targetId)).tap();
    await expect(card).toHaveAttribute("data-focused", "true");

    // Basic typed input still reaches the page without error/crash on a
    // touch device (soft-keyboard specifics — autocomplete bars, key
    // repeat, etc. — are out of scope for this MVP-level check).
    await page.keyboard.type("echo mobile-input-check");
    await page.keyboard.press("Enter");

    // The terminal is still connected afterwards — typing didn't crash the
    // renderer or drop the WebSocket.
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("reconnect/close controls remain reachable (tappable) on a mobile viewport", async ({ page }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.sessionRow(targetId)).tap();

    const closeButton = page.getByTestId(TESTID.terminalClose(targetId));
    await expect(closeButton).toBeVisible();
    // A minimum touch-target size sanity check — buttons that render too
    // small are effectively unusable on a touchscreen.
    const box = await closeButton.boundingBox();
    expect(box).not.toBeNull();
    if (box) {
      expect(box.height).toBeGreaterThan(16);
    }
  });
});
