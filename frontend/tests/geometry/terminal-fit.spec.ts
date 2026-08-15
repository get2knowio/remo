// Browser geometry suite: does the terminal grid stay consistent with the box
// that clips it, and does the remote hear about every change?
//
// WHY THIS IS A BROWSER TEST. jsdom has no layout engine — every element
// measures 0x0 — so the Vitest suite can prove the fit loop's bookkeeping but
// nothing about its geometry. Both defects this suite guards were invisible
// there and only reproducible against real layout:
//
//   * the emulator painting below `.terminal-card`'s `overflow: hidden`, which
//     hides the bottom rows of a TUI (an input box, typically) with no
//     scrollbar and no other symptom;
//   * a container change that never reaches the remote PTY, stranding it at a
//     stale size until the operator forces a different one by hand.
//
// It needs no backend: `page.route` answers the terminal-create call and
// `page.routeWebSocket` plays the service side of `remo-terminal.v1`, so
// TerminalConnection runs its real code path and every resize the card sends is
// observable here.

import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";

interface CardStats {
  id: string;
  visible: boolean;
  surfaceHeight: number;
  screenHeight: number;
  /** Emulator rows, read independently off the DOM. 0 under the WebGL renderer,
   * which paints to a canvas — the `chromium-dom-renderer` project exists so
   * this is populated somewhere. */
  paintedRows: number;
}

/** Resize frames the card sent, per session-target id, oldest first. */
type ResizeLog = Map<string, Array<{ cols: number; rows: number }>>;

/**
 * Stand in for the service: mint a terminal id per session target, then play
 * the WS side of the protocol and record every resize.
 *
 * Routing at the network layer rather than stubbing TerminalConnection keeps
 * the client's real state machine, frame parsing and re-assert timers in play —
 * which is exactly where the behaviour under test lives. The minted id embeds
 * the session-target id so a resize frame can be attributed back to its card.
 */
async function interceptBackend(page: Page): Promise<ResizeLog> {
  const resizes: ResizeLog = new Map();

  await page.route("**/api/v1/terminals", async (route) => {
    if (route.request().method() !== "POST") {
      return route.fallback();
    }
    const body = route.request().postDataJSON() as { session_target_id: string };
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        terminal_id: `term-${body.session_target_id}`,
        ws_token: "tok",
      }),
    });
  });
  // Closing is best-effort in the client; answering it keeps a failed request
  // from surfacing as an error banner, which would change the box being measured.
  await page.route("**/api/v1/terminals/*", async (route) => {
    await route.fulfill({ status: 204, body: "" });
  });

  await page.routeWebSocket(/\/api\/v1\/terminals\//, (ws: WebSocketRoute) => {
    const terminalId = new URL(ws.url()).pathname.split("/").pop() ?? "";
    const targetId = terminalId.replace(/^term-/, "");
    if (!resizes.has(targetId)) {
      resizes.set(targetId, []);
    }
    ws.onMessage((message) => {
      if (typeof message !== "string") {
        return; // keystrokes are binary
      }
      const frame = JSON.parse(message) as { type: string; cols?: number; rows?: number };
      if (frame.type === "resize" && frame.cols && frame.rows) {
        resizes.get(targetId)?.push({ cols: frame.cols, rows: frame.rows });
      }
      if (frame.type === "ping") {
        ws.send(JSON.stringify({ v: 1, type: "pong" }));
      }
    });
    ws.send(JSON.stringify({ v: 1, type: "ready" }));
  });

  return resizes;
}

const stats = (page: Page): Promise<CardStats[]> =>
  page.evaluate(() => window.__geometry.stats());

const settle = (page: Page): Promise<void> =>
  page.evaluate(() => window.__geometry.settle());

async function ready(page: Page): Promise<void> {
  await page.goto("/");
  await page.waitForFunction(() => Boolean(window.__geometry));
  await page.waitForFunction(() =>
    window.__geometry.stats().every((c) => !c.visible || c.screenHeight > 0),
  );
  await settle(page);
}

/**
 * The invariant, asserted after every mutation: what the emulator PAINTED, what
 * the box ALLOWS, and what the remote was TOLD must all agree.
 *
 * Derived rather than read from the emulator's own accounting, so a renderer
 * that disagrees with what it drew cannot hide. `rows` comes from the last
 * resize frame the card actually sent; the cell height falls out of the painted
 * screen; the capacity falls out of the surface. One row too many and the
 * bottom line is clipped by `.terminal-card`'s `overflow: hidden` — invisibly,
 * which is what made the original defect so hard to attribute.
 *
 * Polled: WS frames arrive asynchronously in the Node-side route, so a bare
 * read can race a resize that is still in flight.
 */
async function expectConsistent(
  page: Page,
  resizes: ResizeLog,
  context: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const cards = (await stats(page)).filter((c) => c.visible);
        if (cards.length === 0) {
          return ["no visible cards to measure"];
        }
        const problems: string[] = [];
        for (const card of cards) {
          const last = resizes.get(card.id)?.at(-1);
          if (!last) {
            problems.push(
              `${card.id}: no resize reached the service — the remote would still ` +
                `be at the size its PTY was spawned with`,
            );
            continue;
          }
          if (card.screenHeight > card.surfaceHeight + 0.5) {
            problems.push(
              `${card.id}: paints ${card.screenHeight}px into a ` +
                `${card.surfaceHeight}px surface — the overflow is clipped, not scrollable`,
            );
          }
          // Prefer the independently-painted row count. Deriving the cell
          // height from the rows the card REPORTED would be circular: a card
          // that fitted correctly but never told the remote would still look
          // self-consistent, which is exactly the failure this suite exists to
          // catch. Under WebGL there is nothing to count and we fall back —
          // which is why the same specs also run on the DOM renderer.
          const emulatorRows = card.paintedRows || last.rows;
          if (card.paintedRows && card.paintedRows !== last.rows) {
            problems.push(
              `${card.id}: the emulator is painting ${card.paintedRows} rows but ` +
                `the remote was last told ${last.rows} — a TUI would draw its ` +
                `bottom-anchored UI at a row the browser never shows`,
            );
          }
          const cellHeight = card.screenHeight / emulatorRows;
          const rowsThatFit = Math.floor(card.surfaceHeight / cellHeight);
          if (emulatorRows !== rowsThatFit) {
            problems.push(
              `${card.id}: ${emulatorRows} rows in a surface that fits ` +
                `${rowsThatFit} (cell ${cellHeight.toFixed(2)}px, surface ` +
                `${card.surfaceHeight}px)`,
            );
          }
        }
        return problems;
      },
      { message: context, timeout: 15_000 },
    )
    .toEqual([]);
}

test.describe("terminal geometry", () => {
  test("stays consistent across every layout change", async ({ page }) => {
    const resizes = await interceptBackend(page);
    await ready(page);
    await expectConsistent(page, resizes, "initial grid");

    // Chrome off/on moves the surface by the header's height without touching
    // its width — the vertical-only change that exposed this class of bug.
    await page.evaluate(() => window.__geometry.setChrome(false));
    await expectConsistent(page, resizes, "tile chrome hidden");

    await page.evaluate(() => window.__geometry.setChrome(true));
    await expectConsistent(page, resizes, "tile chrome shown");

    // Maximize hides three cards and re-modes the fourth (grid -> single, which
    // also rescales the font); restoring reverses both at once.
    await page.evaluate(() => window.__geometry.maximize("t0"));
    await expectConsistent(page, resizes, "t0 maximized");

    await page.evaluate(() => window.__geometry.maximize(null));
    await expectConsistent(page, resizes, "restored from maximize");
  });

  test("stays consistent across window resizes", async ({ page }) => {
    const resizes = await interceptBackend(page);
    await ready(page);

    // Vertical-only changes are the interesting ones: they move rows while
    // leaving cols alone, which is the signature of the reported failure.
    for (const [width, height] of [
      [1400, 620],
      [1400, 1100],
      [1000, 520],
      [1400, 900],
      [760, 460],
    ] as const) {
      await page.setViewportSize({ width, height });
      await settle(page);
      await expectConsistent(page, resizes, `viewport ${width}x${height}`);
    }
  });

  test("a card hidden across a resize refits when it is shown again", async ({ page }) => {
    const resizes = await interceptBackend(page);
    await ready(page);

    // While maximized the other three measure 0x0, so the fit loop skips them
    // entirely by design. Resizing now means they come back to a pane that
    // changed size while they could not react to it.
    await page.evaluate(() => window.__geometry.maximize("t0"));
    await page.setViewportSize({ width: 1200, height: 560 });
    await settle(page);

    await page.evaluate(() => window.__geometry.maximize(null));
    await settle(page);
    await expectConsistent(page, resizes, "shown again after a hidden resize");
  });

  test("re-asserts the size after a resize so a dropped SIGWINCH self-heals", async ({
    page,
  }) => {
    const resizes = await interceptBackend(page);
    await ready(page);

    await page.setViewportSize({ width: 1200, height: 640 });
    await settle(page);
    await expectConsistent(page, resizes, "after a resize");

    const settledRows = resizes.get("t0")?.at(-1)?.rows;
    expect(settledRows, "t0 sent a resize").toBeDefined();
    const framesBefore = resizes.get("t0")?.length ?? 0;

    // A resize reaches the remote as exactly one SIGWINCH, and everything below
    // the service PTY has to be listening at that instant. The client therefore
    // re-asserts the same dims on a bounded schedule
    // (RESIZE_REASSERT_DELAYS_MS, topping out at 3s) so a dropped signal gets
    // further chances instead of stranding the remote at a stale size.
    await page.waitForTimeout(3500);
    const framesAfter = resizes.get("t0") ?? [];
    expect(
      framesAfter.length,
      "no re-assert followed the resize — a SIGWINCH dropped by a busy or " +
        "still-starting remote could never be repaired",
    ).toBeGreaterThan(framesBefore);
    // Every re-assert must carry the settled size, not an intermediate one.
    for (const frame of framesAfter.slice(framesBefore)) {
      expect(frame.rows).toBe(settledRows);
    }
  });
});
