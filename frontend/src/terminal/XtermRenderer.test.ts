// Construction-time xterm options that are load-bearing for behaviour we can't
// otherwise reach from a test.
//
// Why these are asserted against the constructed Terminal rather than exercised
// end-to-end: the behaviour they gate is platform-branched inside xterm, on a
// `isMac` it reads from `navigator.platform` at module load. Chromium on a Linux
// CI runner always takes the non-Mac branch, and Playwright's `userAgent`
// override does not change `navigator.platform` — so a browser test of the macOS
// path would silently exercise the Linux one and pass no matter what we set.
// Asserting the option is the honest bound of what is checkable here.

import { describe, expect, it } from "vitest";
import type { Terminal } from "@xterm/xterm";

import { XtermRenderer } from "./XtermRenderer";

/** The renderer owns its Terminal privately; tests read it deliberately. */
function terminalOf(renderer: XtermRenderer): Terminal {
  return (renderer as unknown as { terminal: Terminal }).terminal;
}

describe("XtermRenderer terminal options", () => {
  it("lets macOS users select text while a TUI owns the mouse", () => {
    // xterm's escape hatch from application mouse reporting is asymmetric:
    //   isMac ? altKey && macOptionClickForcesSelection : shiftKey
    // Shift is never consulted on macOS, and this option defaults to false, so
    // leaving it unset means a Mac user inside Claude Code (or vim, or htop)
    // cannot select — and therefore cannot copy — anything at all.
    const renderer = new XtermRenderer();

    expect(terminalOf(renderer).options.macOptionClickForcesSelection).toBe(true);

    renderer.dispose();
  });

  it("enables proposed API so the ligatures addon can load", () => {
    // @xterm/addon-ligatures registers a character joiner behind xterm's
    // proposed-API guard and throws on load without this.
    const renderer = new XtermRenderer();

    expect(terminalOf(renderer).options.allowProposedApi).toBe(true);

    renderer.dispose();
  });
});
