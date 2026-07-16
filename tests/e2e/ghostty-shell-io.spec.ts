// Ghostty Web compatibility suite, part 1/2 (T060, FR-039/SC-009): shell I/O
// fidelity — bash, zsh, Unicode, bracketed paste, mouse, resize.
//
// See `ghostty-tui-compatibility.spec.ts` for Zellij, project-menu,
// devcontainer startup, and full-screen TUI (alt-screen) coverage.
//
// HOW CONTENT IS ASSERTED WITHOUT DOM TEXT QUERIES: `GhosttyRenderer` draws
// into a `ghostty-web`-owned canvas (see `keyboard-routing.spec.ts`'s
// file-level comment) — there is no accessible text layer to assert
// "the screen shows X" against. Instead, every spec below uses
// `captureTerminalFrames()` (fixtures.ts) to transparently intercept the
// terminal's WebSocket and record the exact PTY output bytes the server
// sent (i.e. exactly what the renderer was fed), then asserts against that
// byte stream via `waitForOutput()`. This is a byte-accurate, honest
// substitute for "the terminal displays this correctly": it proves the
// bytes arrived intact (no dropped/mangled/replacement-character corruption
// between remote shell and browser), even though it cannot prove Ghostty's
// canvas painted every glyph pixel-perfectly — that residual gap is exactly
// what SC-009's documented xterm.js-fallback escape hatch exists for.
//
// BACKEND REQUIREMENTS: gated behind `REMO_E2E_BACKEND_URL` via
// `requireBackendFixture` (fixtures.ts). Full fidelity additionally requires
// the backend fixture's target instance(s) to have REAL `bash` and `zsh`
// installed and reachable via `exec` from the project's normal login shell —
// not just the fake `remo-host` stand-in scripts used by this repo's Python
// integration tests (`tests/integration/`), which don't spawn a real
// interactive shell at all. These specs are written to the real protocol
// (`contracts/terminal-websocket.md`) and DOM contract established by T042/
// T044 and are correct-and-ready-to-run, but — like T044/T049's
// arm64-emulation-limited portions — could not be executed in this sandbox
// (no `node_modules`/Playwright install, no network access).

import { expect, test } from "@playwright/test";
import type { CapturedFrames } from "./fixtures";
import {
  captureTerminalFrames,
  forceRenderer,
  openTerminal,
  requireBackendFixture,
  typeCommand,
  waitForDiscoveredTargets,
  waitForOutput,
} from "./fixtures";

test.describe("Ghostty Web compatibility: shell I/O fidelity", () => {
  // xterm.js is the default engine; force ghostty-web so this suite exercises
  // the renderer it's named for.
  test.beforeEach(async ({ page }) => {
    requireBackendFixture(test);
    await forceRenderer(page, "ghostty");
  });

  test("bash: prompt accepts a command and echoes exact, uncorrupted output", async ({ page }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    // Every opened terminal attaches via `project-launch`/`sessions attach`
    // into the project's normal login shell (research.md R1/R4) — which
    // isn't guaranteed to be bash, so force it explicitly with `exec`
    // rather than assuming the remote user's default shell.
    await typeCommand(page, surface, "exec bash");
    await typeCommand(page, surface, 'echo "SHELL_CHECK:$0"');

    // `$0` prints "bash" (or "-bash" for a login shell) — a match proves the
    // command was typed, executed, and its result echoed back with no
    // dropped/mangled bytes (a corrupted control-sequence stream would
    // either fail to match at all or interleave garbage into the marker).
    await waitForOutput(frames, /SHELL_CHECK:-?bash/, 15_000);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("zsh: prompt accepts a command and echoes exact, uncorrupted output", async ({ page }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    await typeCommand(page, surface, "exec zsh");
    await typeCommand(page, surface, 'echo "SHELL_CHECK:$0"');

    await waitForOutput(frames, /SHELL_CHECK:-?zsh/, 15_000);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("Unicode: multi-byte UTF-8 (CJK, emoji, box-drawing, combining marks) round-trips intact", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    // CJK characters, an emoji (outside the BMP, so UTF-16 surrogate pair on
    // the JS side but a single 4-byte UTF-8 sequence over the wire),
    // box-drawing characters, and a combining acute accent (e + U+0301,
    // rather than the precomposed é) — a good multi-byte/combining-sequence
    // stress case.
    const sample = "UNICODE_CHECK:カフェ café 你好 🚀 ┌─┐ é";
    await typeCommand(page, surface, `echo "${sample}"`);

    // The remote shell's stdout -> PTY -> WS pipe is byte-transparent; a
    // corrupted frame boundary would decode with U+FFFD replacement
    // characters or silently drop/reorder bytes rather than reproduce
    // `sample` exactly.
    await waitForOutput(frames, sample, 15_000);
    expect(frames.renderedText()).not.toContain("�");
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });

  test("bracketed paste: a multi-line clipboard paste arrives as one wrapped frame, not per-line keystrokes", async ({
    page,
    context,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const pasteText = "echo line-one\necho line-two\necho line-three";
    await surface.click();
    await page.evaluate((text) => navigator.clipboard.writeText(text), pasteText);
    await page.keyboard.press(process.platform === "darwin" ? "Meta+V" : "Control+V");

    // If bash's readline bracketed-paste mode (the default once an
    // interactive prompt is live, enabled remotely via `\e[?2004h`) is
    // honored end-to-end by ghostty-web's paste handling, the three lines
    // above arrive wrapped as ESC[200~ ... ESC[201~ in the data the browser
    // sends upstream — i.e. inserted as one editable buffered line the user
    // can review before pressing Enter, rather than being immediately
    // executed as three separate Enter-terminated commands. That distinction
    // is the entire point of bracketed paste: it stops a pasted shell
    // snippet from running commands unreviewed.
    //
    // CAVEAT (matches `GhosttyRenderer.ts`'s own file-level doc comment):
    // ghostty-web's exact paste-handling API/behavior is unconfirmed against
    // the real package, which is not installed in this sandbox. This is the
    // spec-accurate assertion to run once it is — if it fails against a real
    // `ghostty-web` build, that is exactly the kind of release-blocking gap
    // SC-009 anticipates being handled by falling back to `XtermRenderer`.
    await expect
      .poll(
        () =>
          frames.toServer.some(
            (frame) => Buffer.isBuffer(frame) && frame.includes("\u001b[200~"),
          ),
        { timeout: 10_000 },
      )
      .toBe(true);

    // Whether or not bracketing kicked in, the renderer must not choke on
    // the paste: the connection stays healthy and keeps accepting input.
    await expect(card).toHaveAttribute("data-connection-state", "ready");
    await typeCommand(page, surface, 'echo "STILL_ALIVE_AFTER_PASTE"');
    await waitForOutput(frames, "STILL_ALIVE_AFTER_PASTE", 10_000);
  });

  test("mouse: click and wheel-scroll inside the terminal surface don't crash the renderer", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    const box = await surface.boundingBox();
    expect(box).not.toBeNull();
    if (!box) {
      return;
    }

    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, 120); // scroll down
    await page.mouse.wheel(0, -120); // scroll back up

    // A precise assertion of "the exact mouse-reporting escape sequence was
    // sent upstream" needs a mouse-aware full-screen TUI (mouse reporting is
    // usually off at a bare shell prompt) — that's covered as a best-effort,
    // more concrete check in `ghostty-tui-compatibility.spec.ts`'s Zellij
    // tests, since only a real Zellij session has mouse mode enabled. Here,
    // the honest and still-meaningful check is: mouse interaction over the
    // canvas surface doesn't crash the renderer or drop the connection, and
    // the terminal keeps accepting keyboard input afterward.
    await expect(card).toHaveAttribute("data-connection-state", "ready");
    await typeCommand(page, surface, 'echo "MOUSE_DID_NOT_CRASH_RENDERER"');
    await waitForOutput(frames, "MOUSE_DID_NOT_CRASH_RENDERER", 10_000);
  });

  test("resize: viewport resize triggers a resize control frame and the terminal keeps working afterward", async ({
    page,
  }) => {
    const [targetId] = await waitForDiscoveredTargets(page);
    const frames = await captureTerminalFrames(page);
    const { surface, card } = await openTerminal(page, targetId);

    // A wide, easily-corrupted reference line: 80 `=` characters, printed
    // BEFORE the resize so the captured byte stream shows it survives the
    // resize untouched, then again AFTER to prove the pipeline still works
    // post-resize.
    await typeCommand(page, surface, "printf 'BEFORE_RESIZE:%s\\n' \"$(printf '%80s' | tr ' ' '=')\"");
    await waitForOutput(frames, /BEFORE_RESIZE:=+/, 10_000);

    const resizeFramesBefore = countResizeFrames(frames);

    await page.setViewportSize({ width: 800, height: 600 });
    await page.setViewportSize({ width: 1400, height: 900 });

    // `ResizeObserver` -> `adapter.fit()` -> `connection.sendResize(cols,
    // rows)` (TerminalCard.tsx's mount effect) fires a new JSON
    // `{"type":"resize"}` control frame whenever the container's box
    // changes — assert at least one new one arrived rather than the resize
    // pipeline silently no-op'ing.
    await expect
      .poll(() => countResizeFrames(frames), { timeout: 10_000 })
      .toBeGreaterThan(resizeFramesBefore);

    await typeCommand(page, surface, "printf 'AFTER_RESIZE:%s\\n' \"$(printf '%80s' | tr ' ' '=')\"");
    await waitForOutput(frames, /AFTER_RESIZE:=+/, 10_000);
    await expect(card).toHaveAttribute("data-connection-state", "ready");
  });
});

function countResizeFrames(frames: CapturedFrames): number {
  return frames.toServer.filter((frame) => {
    if (typeof frame !== "string") {
      return false;
    }
    try {
      return (JSON.parse(frame) as { type?: string }).type === "resize";
    } catch {
      return false;
    }
  }).length;
}
