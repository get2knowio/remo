// The container -> emulator -> remote-PTY fit loop.
//
// Extracted from TerminalCard so the browser geometry suite
// (frontend/tests/geometry) can drive the SHIPPED logic instead of a copy of
// it. That is the whole point of the extraction: a regression test that
// re-implements the thing it guards proves nothing — the copy stays correct
// while the real loop rots. jsdom cannot help here either, since it has no
// layout engine and every element measures 0x0, so this is only ever
// meaningfully exercised in a real browser.
//
// Behaviour is deliberately unchanged from the inline version it replaces.

import type { RendererAdapter, TerminalDimensions } from "./RendererAdapter";

export interface FitLoopOptions {
  /** The renderer to fit, or null when this card has nothing to fit yet. */
  getAdapter: () => Pick<RendererAdapter, "fit"> | null;
  /** The element whose box the emulator must fill. */
  getContainer: () => HTMLElement | null;
  /** Called ONLY when the cell grid actually changed — i.e. when the remote
   * PTY needs to hear about it. */
  onGridChange: (dims: TerminalDimensions) => void;
}

export interface FitLoop {
  /** Request a fit. Coalesced to at most one per animation frame. */
  schedule(): void;
  /** Cancel any pending fit and forget the last-sent grid. */
  dispose(): void;
}

export function createFitLoop(options: FitLoopOptions): FitLoop {
  let raf: number | null = null;
  let lastSent: TerminalDimensions | null = null;

  return {
    schedule(): void {
      if (raf !== null) {
        return; // a fit is already scheduled for this frame
      }
      // Coalesce fit()+resize into at most one per animation frame. A window
      // drag fires the ResizeObserver many times per second; without this each
      // tick would fit() and push a SIGWINCH-triggering resize to the remote
      // PTY (and can trip the browser's "ResizeObserver loop" warning).
      raf = requestAnimationFrame(() => {
        raf = null;
        const adapter = options.getAdapter();
        const container = options.getContainer();
        if (!adapter || !container) {
          return;
        }
        // A hidden pane collapses to 0x0; fitting then would shrink the remote
        // PTY to 1x1 and corrupt a backgrounded TUI. Skip — the observer fires
        // again with real dimensions when the card is shown, and
        // TerminalConnection re-sends the last dims on `ready` after a
        // reconnect.
        if (container.clientWidth === 0 || container.clientHeight === 0) {
          return;
        }
        const dims = adapter.fit();
        if (lastSent && lastSent.cols === dims.cols && lastSent.rows === dims.rows) {
          return; // grid unchanged — no need to resize the remote PTY
        }
        lastSent = dims;
        options.onGridChange(dims);
      });
    },

    dispose(): void {
      if (raf !== null) {
        cancelAnimationFrame(raf);
        raf = null;
      }
      lastSent = null;
    },
  };
}
