// Shared helpers for the Playwright suite: T044 (US3 — grid/tab/focus,
// keyboard routing, reconnect, mobile input) plus T060 (Ghostty Web
// compatibility — `ghostty-shell-io.spec.ts` / `ghostty-tui-compatibility.
// spec.ts`).
//
// Every spec in this directory needs a REAL discovery snapshot (actual
// `SessionTarget`s backed by a running `remo web serve` + reachable
// instances, ideally the same kind of disposable-SSH-container fixture used
// by `tests/integration/test_nine_terminals.py`'s 3x3 grid) in order to open
// terminals and exercise real WebSocket/PTY behavior end to end. A browser
// test cannot stand that fixture up on its own, and mocking the WebSocket/
// REST layer would drift from the real `contracts/terminal-websocket.md`
// protocol and give false confidence about routing/reconnect correctness.
//
// So: every spec calls `requireBackendFixture(test)` first and skips
// cleanly — with a clear reason — when `REMO_E2E_BACKEND_URL` is not set,
// rather than either (a) silently no-op "passing" or (b) faking the
// backend. This mirrors the note in `specs/010-web-session-interface/
// tasks.md` T044 and the parent task's framing: these specs are correct and
// CI-ready, but unexecuted in this sandbox (no npm/Playwright install here).
//
// T060's specs additionally need REAL bash/zsh/zellij/project-menu/
// devcontainer-cli tooling on that backend fixture's target instance(s) —
// meaningfully heavier than the fake `remo-host` stand-in scripts used by
// the Python integration tests elsewhere in this repo. See each T060 spec
// file's own header comment for specifics.

import { expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

/** Set by CI/local dev to point at a `remo web serve` instance with a known
 * discovery fixture available. Unset in this sandbox. */
export const BACKEND_FIXTURE_URL = process.env.REMO_E2E_BACKEND_URL;

/** Minimal structural type covering the one Playwright `test.skip` overload
 * these helpers need, so this file doesn't have to import the full `Test`
 * generic machinery from `@playwright/test`. */
interface Skippable {
  skip(condition: boolean, description?: string): void;
}

/** Call at the top of every `test(...)` body (or in a `test.beforeEach`) to
 * skip the test when no backend fixture is configured. */
export function requireBackendFixture(test: Skippable): void {
  test.skip(
    !BACKEND_FIXTURE_URL,
    "requires a running remo-web backend fixture; set REMO_E2E_BACKEND_URL to enable",
  );
}

/** data-testid prefixes used across `frontend/src/components/*` — kept in
 * one place so a rename only needs updating here. */
export const TESTID = {
  targetCard: (targetId: string) => `target-card-${targetId}`,
  targetOpen: (targetId: string) => `target-open-${targetId}`,
  openAllInstance: (instanceId: string) => `open-all-instance-${instanceId}`,
  openAll: "open-all-button",
  openSelected: "open-selected-button",
  layoutMode: (mode: "grid" | "tabs" | "focused") => `layout-${mode}`,
  workspace: "workspace",
  terminalCard: (targetId: string) => `terminal-card-${targetId}`,
  terminalSurface: (targetId: string) => `terminal-surface-${targetId}`,
  terminalReconnect: (targetId: string) => `terminal-reconnect-${targetId}`,
  terminalClose: (targetId: string) => `terminal-close-${targetId}`,
  tab: (targetId: string) => `tab-${targetId}`,
} as const;

/** Navigates to the dashboard and waits for at least one discovered target
 * to render. Returns the resolved discovery targets' testids (the trailing
 * `id` segment of each `target-card-*` element) in DOM order. */
export async function waitForDiscoveredTargets(page: Page): Promise<string[]> {
  await page.goto("/");
  const targetCards = page.locator('[data-testid^="target-card-"]');
  await targetCards.first().waitFor({ state: "visible", timeout: 15_000 });

  const testIds = await targetCards.evaluateAll((elements) =>
    elements.map((element) => element.getAttribute("data-testid") ?? ""),
  );
  return testIds.map((testId) => testId.replace(/^target-card-/, "")).filter((id) => id.length > 0);
}

/** Reads currently-open terminal-card target ids from the DOM, in render
 * order (GridView/TabView order == workspace.openTargetIds order). */
export async function openTerminalCardIds(page: Page): Promise<string[]> {
  const cards = page.locator('[data-testid^="terminal-card-"]');
  const testIds = await cards.evaluateAll((elements) =>
    elements.map((element) => element.getAttribute("data-testid") ?? ""),
  );
  return testIds.map((testId) => testId.replace(/^terminal-card-/, "")).filter((id) => id.length > 0);
}

/** Opens the given target's terminal (clicking `TESTID.targetOpen`) and
 * waits for it to reach `data-connection-state="ready"`. Returns the card
 * and surface locators — nearly every T060 compatibility spec needs both,
 * so this centralizes the open+wait boilerplate rather than repeating it in
 * every spec file (as `workspace-layout.spec.ts` etc. do inline today). */
export async function openTerminal(
  page: Page,
  targetId: string,
): Promise<{ card: Locator; surface: Locator }> {
  await page.getByTestId(TESTID.targetOpen(targetId)).click();
  const card = page.getByTestId(TESTID.terminalCard(targetId));
  await expect(card).toHaveAttribute("data-connection-state", "ready", { timeout: 20_000 });
  return { card, surface: page.getByTestId(TESTID.terminalSurface(targetId)) };
}

/** Clicks a terminal surface to focus it, types `command`, and presses
 * Enter — the standard "run a command" gesture used across the T060
 * compatibility suite. */
export async function typeCommand(page: Page, surface: Locator, command: string): Promise<void> {
  await surface.click();
  await page.keyboard.type(command);
  await page.keyboard.press("Enter");
}

/**
 * Frames captured by `captureTerminalFrames` below, split by direction.
 * `toServer` is everything the BROWSER sent (keystrokes/paste as binary
 * frames, JSON control frames like `resize`); `toClient` is everything the
 * SERVER sent (PTY output as binary frames, JSON control frames like
 * `ready`/`exit`/`error` per `contracts/terminal-websocket.md`).
 */
export interface CapturedFrames {
  toServer: (string | Buffer)[];
  toClient: (string | Buffer)[];
  /** Concatenates every BINARY `toClient` frame captured so far, decoded as
   * UTF-8 — i.e. the raw PTY output byte stream the renderer would have
   * drawn, with JSON control frames excluded. This is the load-bearing
   * technique this whole suite relies on: `GhosttyRenderer` draws to a
   * `ghostty-web`-owned canvas (see `keyboard-routing.spec.ts`'s file-level
   * comment), so rendered terminal *content* is not DOM-text-queryable from
   * Playwright. Inspecting the WebSocket byte stream the renderer was FED
   * is the honest, protocol-level substitute: it proves the bytes arrived
   * correctly (byte-accurate, no mangled/dropped/replacement-character
   * corruption) even though it can't prove the canvas painted every glyph
   * pixel-perfectly. */
  renderedText(): string;
}

/**
 * Installs a passthrough `page.routeWebSocket` interceptor on the terminal
 * WebSocket endpoint (`/api/v1/terminals/{id}`, matching the URL built by
 * `openTerminalSocket()` in `frontend/src/api/client.ts`) and records every
 * frame in both directions while transparently forwarding it, so the
 * terminal behaves exactly as it would unintercepted.
 *
 * MUST be called before the terminal is opened (i.e. before
 * `openTerminal()`/clicking `TESTID.targetOpen`), same ordering requirement
 * as `reconnect.spec.ts`'s use of `page.routeWebSocket` — the route has to
 * be registered before the page's `new WebSocket(...)` call it targets.
 */
export async function captureTerminalFrames(page: Page): Promise<CapturedFrames> {
  const toServer: (string | Buffer)[] = [];
  const toClient: (string | Buffer)[] = [];

  await page.routeWebSocket(/\/api\/v1\/terminals\//, (ws) => {
    const server = ws.connectToServer();
    ws.onMessage((message) => {
      toServer.push(message);
      server.send(message);
    });
    server.onMessage((message) => {
      toClient.push(message);
      ws.send(message);
    });
  });

  return {
    toServer,
    toClient,
    renderedText(): string {
      return toClient
        .filter((frame): frame is Buffer => Buffer.isBuffer(frame))
        .map((frame) => frame.toString("utf-8"))
        .join("");
    },
  };
}

/** Polls `frames.renderedText()` until it matches `needle` (substring or
 * regexp), or fails after `timeoutMs`. The standard way this suite waits for
 * expected PTY output to arrive asynchronously over the WebSocket. */
export async function waitForOutput(
  frames: CapturedFrames,
  needle: string | RegExp,
  timeoutMs = 15_000,
): Promise<void> {
  await expect
    .poll(() => frames.renderedText(), {
      timeout: timeoutMs,
      message: `waiting for terminal output matching ${String(needle)}`,
    })
    .toMatch(needle);
}
