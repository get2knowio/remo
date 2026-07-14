// Per-terminal reconnect controls + isolation (US3 scenario 3, FR-032):
// "Given one terminal disconnects, when the others remain healthy, then
// they continue without interruption and only the failed terminal shows
// reconnect controls." See fixtures.ts for the REMO_E2E_BACKEND_URL gating
// rationale.
//
// Simulating a real disconnect: `TerminalConnection` reconnects by calling
// `createTerminal()` again on every retry (a brand-new terminal_id each
// time — see `terminal/TerminalConnection.ts`), so a fresh WebSocket
// connection can't be targeted by a stable terminal_id across multiple
// auto-reconnect attempts. Rather than trying to correlate that moving
// target, this spec uses `page.routeWebSocket()` (Playwright 1.48+) in a
// simple "kill mode" that's active ONLY while the victim terminal is being
// opened (before any other terminal exists), so every socket seen during
// that window unambiguously belongs to the victim. Kill mode is switched
// off before any sibling terminal is opened, so siblings are never at risk
// of being caught by the same interception.

import { expect, test } from "@playwright/test";
import { TESTID, requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.describe("reconnect controls", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("a disconnected terminal shows manual reconnect controls while siblings stay healthy", async ({
    page,
  }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(1);
    const [victimId, ...otherIds] = discoveredIds;

    let killModeActive = true;
    await page.routeWebSocket(/\/api\/v1\/terminals\//, (ws) => {
      if (killModeActive) {
        // Refuse the connection outright — simulates the victim's SSH
        // ControlMaster/PTY being unreachable, for every one of
        // TerminalConnection's bounded auto-reconnect attempts.
        ws.close();
        return;
      }
      ws.connectToServer();
    });

    await page.getByTestId(TESTID.targetOpen(victimId)).click();

    // Auto-reconnect (bounded, with backoff) exhausts, then the manual
    // "Reconnect" control appears (FR-020/FR-032).
    await expect(page.getByTestId(TESTID.terminalReconnect(victimId))).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId(TESTID.terminalCard(victimId))).not.toHaveAttribute(
      "data-connection-state",
      "ready",
    );

    // Stop intercepting before opening any sibling — from here on, new
    // sockets connect through to the real backend untouched.
    killModeActive = false;

    for (const id of otherIds) {
      await page.getByTestId(TESTID.targetOpen(id)).click();
    }
    for (const id of otherIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toHaveAttribute(
        "data-connection-state",
        "ready",
        { timeout: 20_000 },
      );
      // Only the victim shows a Reconnect control — healthy siblings never
      // render one (`needsManualReconnect` gates the button in
      // TerminalCard.tsx).
      await expect(page.getByTestId(TESTID.terminalReconnect(id))).toHaveCount(0);
    }

    // The victim's own manual Reconnect recovers it independently of its
    // siblings, which remain untouched throughout.
    await page.getByTestId(TESTID.terminalReconnect(victimId)).click();
    await expect(page.getByTestId(TESTID.terminalCard(victimId))).toHaveAttribute(
      "data-connection-state",
      "ready",
      { timeout: 20_000 },
    );
    for (const id of otherIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toHaveAttribute(
        "data-connection-state",
        "ready",
      );
    }
  });

  test("closing one terminal removes only that card, siblings stay open", async ({ page }) => {
    const discoveredIds = await waitForDiscoveredTargets(page);
    expect(discoveredIds.length).toBeGreaterThan(1);
    const [closedId, ...remainingIds] = discoveredIds;

    await page.getByTestId(TESTID.openAll).click();
    for (const id of discoveredIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toBeVisible();
    }

    await page.getByTestId(TESTID.terminalClose(closedId)).click();

    await expect(page.getByTestId(TESTID.terminalCard(closedId))).toHaveCount(0);
    for (const id of remainingIds) {
      await expect(page.getByTestId(TESTID.terminalCard(id))).toBeVisible();
    }
  });
});
