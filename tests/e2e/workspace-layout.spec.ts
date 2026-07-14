// Grid/tab/focus layout switching + bulk-open (US3 scenarios 1 & 4,
// FR-030/FR-031). See fixtures.ts for why these are gated behind
// REMO_E2E_BACKEND_URL and unexecuted in this sandbox.

import { expect, test } from "@playwright/test";
import { TESTID, openTerminalCardIds, requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.describe("workspace layout", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("open all creates one independently-connected terminal per target (US3 scenario 1)", async ({
    page,
  }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(0);

    await page.getByTestId(TESTID.openAll).click();

    await page.getByTestId(TESTID.workspace).waitFor({ state: "visible" });
    const openIds = await openTerminalCardIds(page);
    expect(openIds.sort()).toEqual(discoveredIds.sort());

    // Each opened terminal reaches "ready" independently — no shared
    // failure/success state across cards (per-terminal progress/error, per
    // scenario 1's "connect independently with per-terminal progress and
    // error state").
    for (const id of openIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toHaveAttribute(
        "data-connection-state",
        "ready",
        { timeout: 20_000 },
      );
    }
  });

  test("grid mode renders every open terminal simultaneously, none hidden", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("grid")).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThan(1);

    // Grid mode: every terminal-card is visible at once (nothing behind a
    // `display: none` tab pane).
    for (const id of openIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toBeVisible();
    }
  });

  test("tabs mode shows a tab strip and switches the visible pane on click", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("tabs")).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThan(1);
    const [firstId, secondId] = openIds;

    // Only one pane is CSS-visible at a time...
    await page.getByTestId(TESTID.tab(firstId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeVisible();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toBeHidden();

    await page.getByTestId(TESTID.tab(secondId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toBeVisible();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeHidden();

    // ...but BOTH stay mounted with a live connection throughout (US3
    // scenario 3: hidden terminals remain connected, not torn down). If
    // switching tabs unmounted/reconnected the hidden card, this attribute
    // would transiently drop to "connecting"/"disconnected" instead of
    // staying "ready".
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute(
      "data-connection-state",
      "ready",
    );
  });

  test("focused mode shows a single pane with no clickable tab strip", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("focused")).click();

    const openIds = await openTerminalCardIds(page);
    // No tab buttons rendered in focused mode (minimal chrome).
    for (const id of openIds) {
      await expect(page.getByTestId(TESTID.tab(id))).toHaveCount(0);
    }
    // Exactly one card visible.
    const visibleCount = await Promise.all(
      openIds.map(async (id) => (await page.getByTestId(TESTID.terminalCard(id)).isVisible()) ? 1 : 0),
    );
    expect(visibleCount.reduce((a, b) => a + b, 0)).toBe(1);
  });

  test("layout mode persists across a reload (FR-034, localStorage only)", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("tabs")).click();

    const stored = await page.evaluate(() => window.localStorage.getItem("remo-web:workspace"));
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? "{}")).toMatchObject({ layoutMode: "tabs" });

    await page.reload();
    await expect(page.getByTestId(TESTID.layoutMode("tabs"))).toHaveClass(/dashboard-layout-button--active/);
  });
});
