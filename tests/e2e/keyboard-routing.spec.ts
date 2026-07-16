// Keyboard input routing + focus / number-jump (console redesign, US3
// scenarios 2 & 3, FR-031). See fixtures.ts for the REMO_E2E_BACKEND_URL
// gating rationale.
//
// DOM-observable invariants (renderer-agnostic):
//   - exactly one terminal-card carries `data-focused="true"` at a time, and
//     it's the one just clicked or jumped to.
//   - a terminal's `data-connection-state` never drops out of "ready" merely
//     because it stopped being the focused/visible one ("hidden terminals
//     remain connected").
//
// Literal rendered terminal *content* is intentionally NOT asserted (Ghostty
// draws to a WASM canvas that isn't text-queryable from Playwright).

import { expect, test } from "@playwright/test";
import { TESTID, openTerminalCardIds, requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.describe("keyboard input routing", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("clicking a terminal's surface moves focus there and nowhere else", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThan(1);
    const [firstId, secondId] = openIds;

    await page.getByTestId(TESTID.terminalSurface(firstId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute("data-focused", "true");
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toHaveAttribute("data-focused", "false");

    await page.getByTestId(TESTID.terminalSurface(secondId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toHaveAttribute("data-focused", "true");
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute("data-focused", "false");

    // The previously-focused terminal is merely unfocused, not disconnected.
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute(
      "data-connection-state",
      "ready",
    );
  });

  test("number keys 1–9 open the numbered session solo", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThanOrEqual(2);
    const [firstId, secondId] = discoveredIds;

    await page.keyboard.press("1");
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeVisible();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute("data-focused", "true");

    // Pressing "2" solos the second target — the first is hidden but stays
    // connected once it has been opened.
    await page.keyboard.press("2");
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toBeVisible();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toHaveAttribute("data-focused", "true");
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toBeHidden();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute(
      "data-connection-state",
      "ready",
    );
  });

  test("hidden terminals stay connected while soloing repeatedly", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThan(1);

    // Solo each terminal in turn, then collapse back to the grid.
    for (const id of openIds) {
      await page.getByTestId(TESTID.terminalCard(id)).click();
      await page.keyboard.press("Escape");
    }

    for (const id of openIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toHaveAttribute(
        "data-connection-state",
        "ready",
      );
    }
  });

  test("provider/instance/project identity stays visible regardless of focus (US3 scenario 4)", async ({
    page,
  }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();

    const openIds = await openTerminalCardIds(page);
    for (const id of openIds) {
      const header = page.getByTestId(TESTID.terminalCard(id)).locator(".terminal-card-identity");
      await expect(header).toBeVisible();
      await expect(header.locator(".terminal-card-project")).not.toBeEmpty();
    }
  });
});
