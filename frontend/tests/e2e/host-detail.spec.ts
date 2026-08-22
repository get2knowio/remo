// Host detail overlay smoke (plan §2.4): clicking a host NAME in the rail
// opens the full-screen detail page; Esc closes it. Deliberately tolerant of
// `features.host_admin` being off — the smoke only exercises the ungated
// read-only surface (the page itself and its dismissal), never a maintenance
// affordance. See fixtures.ts for why this is gated on REMO_E2E_BACKEND_URL.

import { expect, test } from "@playwright/test";
import { requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.describe("host detail page", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("opens via the rail host name and closes on Esc", async ({ page }) => {
    // Discovery must have landed so a host group (and its name button) exists.
    await waitForDiscoveredTargets(page);

    const hostName = page.locator('[data-testid^="host-name-"]').first();
    await hostName.click();

    const detail = page.getByTestId("host-detail-page");
    await expect(detail).toBeVisible();
    // The overlay shows the host's status pill regardless of host_admin.
    await expect(page.getByTestId("host-detail-status")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(detail).toBeHidden();
  });

  test("clicking the host name does not collapse the group", async ({ page }) => {
    await waitForDiscoveredTargets(page);

    const rowsBefore = await page.locator('[data-testid^="session-row-"]').count();
    await page.locator('[data-testid^="host-name-"]').first().click();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("host-detail-page")).toBeHidden();

    // The Part 1 contract: name = detail, chevron/header = collapse. The
    // group's rows are still rendered after the round trip.
    const rowsAfter = await page.locator('[data-testid^="session-row-"]').count();
    expect(rowsAfter).toBe(rowsBefore);
  });
});
