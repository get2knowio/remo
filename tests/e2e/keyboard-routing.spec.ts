// Keyboard input routing + focus cycling (US3 scenarios 2 & 3, FR-031).
// See fixtures.ts for the REMO_E2E_BACKEND_URL gating rationale.
//
// What CAN be asserted reliably from the DOM regardless of which renderer
// (`GhosttyRenderer` default vs. `XtermRenderer` fallback) is active:
//   - exactly one terminal-card carries the "focused" state at a time
//     (`data-focused="true"` / `.terminal-card--focused`), and it's always
//     the one the user just clicked or cycled to.
//   - a terminal's `data-connection-state` never drops out of "ready" just
//     because it stopped being the focused/visible one — this is the
//     concrete, DOM-observable form of "hidden terminals remain connected".
//
// What is intentionally NOT asserted here: literal rendered terminal
// *content* (e.g. "typed marker echoes back only in the focused pane").
// `GhosttyRenderer` draws to a canvas via WASM, which isn't text-queryable
// from Playwright without an app-exposed serialize()/test hook that doesn't
// exist today. If/when such a hook is added (or the suite runs against the
// `XtermRenderer` fallback, which does expose an accessible text layer),
// extend these specs with a real "marker only appears in the focused pane"
// assertion — the DOM-state assertions below are a necessary but not
// exhaustive proxy for FR-031/scenario 2's "input sent only to that
// terminal".

import { expect, test } from "@playwright/test";
import { TESTID, openTerminalCardIds, requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.describe("keyboard input routing", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("clicking a terminal's surface moves focus there and nowhere else", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("grid")).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThan(1);
    const [firstId, secondId] = openIds;

    await page.getByTestId(TESTID.terminalSurface(firstId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute("data-focused", "true");
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toHaveAttribute("data-focused", "false");

    await page.getByTestId(TESTID.terminalSurface(secondId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(secondId))).toHaveAttribute("data-focused", "true");
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute("data-focused", "false");

    // The previously-focused terminal is merely unfocused, not disconnected
    // (US3 scenario 3).
    await expect(page.getByTestId(TESTID.terminalCard(firstId))).toHaveAttribute(
      "data-connection-state",
      "ready",
    );
  });

  test("Ctrl+Shift+ArrowRight/Left cycles focus among open terminals (T048)", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("grid")).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThanOrEqual(3);

    async function currentlyFocused(): Promise<string> {
      for (const id of openIds) {
        const value = await page.getByTestId(TESTID.terminalCard(id)).getAttribute("data-focused");
        if (value === "true") {
          return id;
        }
      }
      throw new Error("no terminal-card currently focused");
    }

    const startId = await currentlyFocused();
    const startIndex = openIds.indexOf(startId);

    await page.keyboard.press("Control+Shift+ArrowRight");
    const afterForward = await currentlyFocused();
    expect(afterForward).toBe(openIds[(startIndex + 1) % openIds.length]);

    await page.keyboard.press("Control+Shift+ArrowLeft");
    const afterBackward = await currentlyFocused();
    expect(afterBackward).toBe(startId);

    // Wrapping: cycling backward from the first target lands on the last.
    for (let i = 0; i < startIndex; i += 1) {
      await page.keyboard.press("Control+Shift+ArrowLeft");
    }
    await page.keyboard.press("Control+Shift+ArrowLeft");
    const wrapped = await currentlyFocused();
    expect(wrapped).toBe(openIds[openIds.length - 1]);
  });

  test("hidden terminals stay connected while cycling focus repeatedly", async ({ page }) => {
    await waitForDiscoveredTargets(page);
    await page.getByTestId(TESTID.openAll).click();
    await page.getByTestId(TESTID.layoutMode("tabs")).click();

    const openIds = await openTerminalCardIds(page);
    expect(openIds.length).toBeGreaterThan(1);

    // Cycle focus through every open terminal and back.
    for (let i = 0; i < openIds.length + 1; i += 1) {
      await page.keyboard.press("Control+Shift+ArrowRight");
    }

    // Every terminal — focused or not — is still "ready", never having been
    // torn down by a focus change.
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
      await expect(header.locator(".terminal-card-instance")).not.toBeEmpty();
      await expect(header.locator(".terminal-card-project")).not.toBeEmpty();
    }
  });
});
