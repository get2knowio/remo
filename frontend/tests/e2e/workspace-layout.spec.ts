// Rail-driven single↔grid workspace (console redesign, US3 scenarios 1 & 4,
// FR-030/FR-031). Clicking a rail row opens a target solo; "Open all" fills a
// responsive grid; clicking a grid tile solos it; Esc collapses back to the
// focused terminal. Hidden-but-attached terminals stay connected. See
// fixtures.ts for why these are gated behind REMO_E2E_BACKEND_URL.

import { expect, test } from "@playwright/test";
import {
  TESTID,
  addTerminalToGrid,
  openTerminalCardIds,
  requireBackendFixture,
  waitForDiscoveredTargets,
} from "./fixtures";

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
    // failure/success state across cards.
    for (const id of openIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toHaveAttribute(
        "data-connection-state",
        "ready",
        { timeout: 20_000 },
      );
    }
  });

  test("open all renders every open terminal simultaneously in the grid", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(1);
    await page.getByTestId(TESTID.openAll).click();

    const openIds = await openTerminalCardIds(page);
    // Two-plus visible → grid: every card is visible at once.
    for (const id of openIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toBeVisible();
    }
  });

  test("the ◻ control solos a grid tile; Esc returns to the grid", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(1);
    await page.getByTestId(TESTID.openAll).click();
    const openIds = await openTerminalCardIds(page);
    const [firstId, secondId] = openIds;

    // Solo the first tile via its "fill the main pane" control (the header is a
    // drag handle now, not a click-to-solo target). Only it stays visible.
    await page.getByTestId(TESTID.terminalNormal(firstId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeVisible();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toBeHidden();
    // Hidden card keeps its live connection (US3 scenario 3).
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toHaveAttribute(
      "data-connection-state",
      "ready",
    );

    // Esc collapses back to the remembered grid.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toBeVisible();
  });

  test("⌘-click / add-to-grid builds a grid without soloing", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(1);
    const [firstId, secondId] = discoveredIds;

    await page.getByTestId(TESTID.sessionRow(firstId)).click(); // solo first
    await addTerminalToGrid(page, secondId); // add second → grid of two

    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeVisible();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toBeVisible();
  });

  test("open set persists across a reload (FR-034, localStorage only)", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    const [firstId] = discoveredIds;
    await page.getByTestId(TESTID.sessionRow(firstId)).click();

    const stored = await page.evaluate(() => window.localStorage.getItem("remo-web:workspace"));
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? "{}")).toMatchObject({
      attached: [firstId],
      visible: [firstId],
      focusedId: firstId,
    });

    await page.reload();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeVisible();
  });
});
