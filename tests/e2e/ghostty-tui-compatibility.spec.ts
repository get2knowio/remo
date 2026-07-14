// Ghostty Web compatibility suite, part 2/2 (T060, FR-039/SC-009): Zellij,
// the project menu/launch path, devcontainer startup, and full-screen TUIs
// (alternate-screen buffer handling).
//
// See `ghostty-shell-io.spec.ts` for bash/zsh/Unicode/paste/mouse/resize
// coverage and for the "why WebSocket-byte-stream assertions instead of DOM
// text queries" rationale (`GhosttyRenderer` draws to a canvas — no
// accessible text layer). Both files share `captureTerminalFrames()` /
// `waitForOutput()` from `fixtures.ts`.
//
// WHY EVERY OPENED TERMINAL ALREADY EXERCISES ZELLIJ + DEVCONTAINER STARTUP:
// per research.md R1/R4, `remo-host sessions attach --project NAME` (the RPC
// the web backend issues for every `TESTID.targetOpen` click) is literally
// `exec ~/.local/bin/project-launch --project NAME` — the SAME entry point
// `remo shell -p` uses (SC-002 parity), which lands inside the project's
// Zellij session and, if its devcontainer isn't already running, streams
// startup output before the shell appears. So the Zellij/devcontainer
// compatibility checks below don't need any special setup beyond opening a
// terminal — they inspect the captured byte stream from that same open.
//
// PROJECT-MENU SCOPE NOTE: the web attach path deliberately never shells out
// to the interactive `project-menu` fzf picker (research.md R1's explicitly
// rejected alternative: scraping its ANSI-laden output; FR-059 reiterates
// the UI must not fall back to it). What FR-039/SC-009's "project menu/
// launch" bullet still covers here is a user manually running `project-menu`
// from within an already-open browser terminal, same as at a local SSH
// prompt — see the dedicated test below.
//
// BACKEND REQUIREMENTS: gated behind `REMO_E2E_BACKEND_URL` via
// `requireBackendFixture` (fixtures.ts). Full fidelity additionally requires
// the backend fixture's target instance(s) to have REAL `zellij`,
// `project-launch`/`project-menu`, a devcontainer CLI, and `vim` (or another
// full-screen TUI) installed and reachable — not just the fake `remo-host`
// stand-in scripts used by this repo's Python integration tests
// (`tests/integration/`), which don't run any of that real tooling. These
// specs are written to the real protocol (`contracts/terminal-websocket.md`)
// and DOM contract established by T042/T044 and are correct-and-ready-to-
// run, but — like T044/T049's arm64-emulation-limited portions — could not
// be executed in this sandbox (no `node_modules`/Playwright install, no
// network access).

import { expect, test } from "@playwright/test";
import {
  captureTerminalFrames,
  openTerminal,
  requireBackendFixture,
  typeCommand,
  waitForDiscoveredTargets,
  waitForOutput,
} from "./fixtures";

const ALT_SCREEN_ENABLE = "\u001b[?1049h";
const ALT_SCREEN_DISABLE = "\u001b[?1049l";
// Unicode box-drawing block (U+2500-U+257F) — Zellij's pane borders and
// status bar rely on these; their presence is a renderer-agnostic signal
// that a real Zellij TUI painted, not a bare shell prompt.
const BOX_DRAWING = /[─-╿]/;

test.describe("Ghostty Web compatibility: Zellij and full-screen TUIs", () => {
  test.beforeEach(() => {
    requireBackendFixture(test);
  });

  test("Zellij: attaching to a project lands in a real Zellij session (alt-screen + styled borders)", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { card } = await openTerminal(page, targetId);

    // Zellij switches to the alternate screen buffer and paints a styled
    // status bar with unicode box-drawing pane borders — both are strong
    // signals the full Zellij TUI rendered (not just a shell prompt), and
    // both are heavy stress tests for a terminal renderer (background
    // colors, cursor positioning, multi-byte glyphs).
    await expect.poll(() => frames.renderedText().includes(ALT_SCREEN_ENABLE), { timeout: 15_000 }).toBe(true);
    expect(BOX_DRAWING.test(frames.renderedText())).toBe(true);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("Zellij: the detach keybinding (Ctrl+o d) exits cleanly, no renderer/protocol error", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface } = await openTerminal(page, targetId);

    await expect.poll(() => frames.renderedText().includes(ALT_SCREEN_ENABLE), { timeout: 15_000 }).toBe(true);

    await surface.click();
    // Zellij's default detach chord: Ctrl+o opens the session/pane menu,
    // then 'd' detaches.
    await page.keyboard.press("Control+o");
    await page.keyboard.press("d");

    // Detaching ends this particular `project-launch`/zellij-attach process
    // (it was `exec`'d, replacing the SSH session's process image), so the
    // PTY exits and the server sends a control frame ending the connection.
    // Per `contracts/terminal-websocket.md` that MUST be a graceful
    // `{"type":"exit",...}` frame, never `{"type":"error",...}` — an error
    // frame here would mean the renderer or transport choked on Zellij's
    // detach/teardown escape sequences. (SC-014 also requires the remote
    // Zellij *session* itself to survive this — that's a backend-side
    // property, not independently observable from the browser, and is
    // exercised by `tests/integration/test_nine_terminals.py`-style
    // fixtures rather than here.)
    await expect
      .poll(
        () =>
          frames.toClient.some((frame) => typeof frame === "string" && frame.includes('"type":"exit"')) ||
          frames.toClient.some((frame) => typeof frame === "string" && frame.includes('"type":"error"')),
        { timeout: 15_000 },
      )
      .toBe(true);

    const sawError = frames.toClient.some(
      (frame) => typeof frame === "string" && frame.includes('"type":"error"'),
    );
    expect(sawError).toBe(false);
  });

  test("full-screen TUI: vim's alternate-screen buffer enters and exits cleanly, terminal usable after", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    await typeCommand(page, surface, "exec bash");
    await waitForOutput(frames, ALT_SCREEN_ENABLE, 15_000); // Zellij's own alt-screen; confirms the shell is ready

    const beforeVim = frames.renderedText().length;
    await typeCommand(page, surface, "vim");

    // vim uses the same `?1049h`/`?1049l` alternate-screen pair as Zellij
    // itself (it's the standard convention) — assert a NEW enable sequence
    // arrives after launching vim, i.e. vim's own alt-screen switch nested
    // inside Zellij's pane, not just the one Zellij already sent at attach.
    await expect
      .poll(() => countOccurrences(frames.renderedText().slice(beforeVim), ALT_SCREEN_ENABLE), {
        timeout: 10_000,
      })
      .toBeGreaterThan(0);

    await page.keyboard.press("Escape");
    await page.keyboard.type(":q");
    await page.keyboard.press("Enter");

    // Exiting vim restores (disables) the alternate screen — a corrupted
    // restore would leave the renderer showing vim's stale screen, or
    // subsequent input silently swallowed because parser/cursor state was
    // left inconsistent.
    await expect
      .poll(() => countOccurrences(frames.renderedText().slice(beforeVim), ALT_SCREEN_DISABLE), {
        timeout: 10_000,
      })
      .toBeGreaterThan(0);

    await typeCommand(page, surface, 'echo "TERMINAL_USABLE_AFTER_VIM"');
    await waitForOutput(frames, "TERMINAL_USABLE_AFTER_VIM", 10_000);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("devcontainer startup: verbose multi-line progress output doesn't corrupt terminal state", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);

    // `openTerminal` waits for "ready", which for a target whose
    // devcontainer isn't already running only happens AFTER `project-launch`
    // finishes building/starting it and streaming that (often
    // carriage-return-heavy progress-bar) output into this same terminal
    // (spec.md US1 scenario 2). If the fixture's discovered target already
    // has a running devcontainer, this still exercises whatever startup
    // chatter `project-launch`/Zellij print on attach — any verbose,
    // CR-heavy preamble is a valid stress case, so this test doesn't depend
    // on forcing a guaranteed cold start.
    const { surface, card } = await openTerminal(page, targetId);

    // A corrupted decode boundary — e.g. a `\r`-heavy progress bar or a
    // multi-byte UTF-8 sequence straddling two WS frames — would show up as
    // a stray replacement character in the accumulated byte stream.
    expect(frames.renderedText()).not.toContain("�");

    // The strongest usable signal that startup output didn't leave the
    // parser/cursor state corrupted: a command typed immediately afterward
    // still round-trips correctly.
    await typeCommand(page, surface, 'echo "USABLE_AFTER_STARTUP_OUTPUT"');
    await waitForOutput(frames, "USABLE_AFTER_STARTUP_OUTPUT", 15_000);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("project-menu: the interactive fzf picker accepts arrow-key navigation and Escape cancels cleanly", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    await typeCommand(page, surface, "exec bash");
    await waitForOutput(frames, ALT_SCREEN_ENABLE, 15_000);

    const beforeMenu = frames.renderedText().length;
    await typeCommand(page, surface, "project-menu");

    // fzf repaints the screen with heavy escape-sequence use (cursor
    // positioning, SGR color) for its list UI — assert SOME escape-sequence
    // activity happened after launching it, i.e. it actually drew an
    // interactive picker rather than printing plain text or erroring with
    // "command not found".
    await expect
      .poll(() => frames.renderedText().slice(beforeMenu).includes("\u001b["), { timeout: 10_000 })
      .toBe(true);

    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowUp");
    // Cancel out without selecting a project — avoids the side effect of
    // actually attaching to a (possibly different) project mid-test.
    await page.keyboard.press("Escape");

    await typeCommand(page, surface, 'echo "TERMINAL_USABLE_AFTER_PROJECT_MENU"');
    await waitForOutput(frames, "TERMINAL_USABLE_AFTER_PROJECT_MENU", 10_000);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });
});

/** Counts non-overlapping occurrences of `needle` in `haystack`. */
function countOccurrences(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}
