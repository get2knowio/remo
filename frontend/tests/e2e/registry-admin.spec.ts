// Registry-admin smoke (023): with REMO_WEB_REGISTRY_ADMIN unset (the e2e
// backend's default posture), the console renders NO add-host affordance and
// the gated API is dormant — a POST answers the same 404 an unknown route
// does, through the real stack. See fixtures.ts for the REMO_E2E_BACKEND_URL
// gating rationale.

import { expect, test } from "@playwright/test";
import { requireBackendFixture, waitForDiscoveredTargets } from "./fixtures";

test.describe("registry admin dormancy", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("flag off: no affordance in the rail, dormant 404 on the API", async ({ page }) => {
    await waitForDiscoveredTargets(page);

    await expect(page.getByTestId("rail-add-host")).toHaveCount(0);

    const gated = await page.request.post("/api/v1/registry/hosts", {
      data: { name: "x", target: "y" },
    });
    const unknown = await page.request.get("/api/v1/definitely-not-a-route");
    expect(gated.status()).toBe(404);
    expect(unknown.status()).toBe(404);
    expect(await gated.text()).toBe(await unknown.text());
  });
});
