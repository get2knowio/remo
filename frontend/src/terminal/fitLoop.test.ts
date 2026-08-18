// The fit loop's OBSERVABLE state — what it last sent, whether a fit is queued,
// and why the last scheduled one sent nothing.
//
// The fit itself can only be tested for real in a browser (jsdom has no layout
// engine; see frontend/tests/geometry). What is testable here is the
// bookkeeping the diagnostics snapshot reads, and it earns its keep precisely
// because the skip reasons are otherwise invisible: "hidden" and "unchanged"
// are both silent early returns that look identical from outside — one means
// the pane is parked behind display:none, the other means the grid genuinely
// did not move.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createFitLoop, type FitLoop } from "./fitLoop";
import type { RendererAdapter, TerminalDimensions } from "./RendererAdapter";

/** Deferred rAF: tests decide when the frame runs. */
let frames: Array<() => void> = [];

function runFrame(): void {
  const queued = frames;
  frames = [];
  for (const fn of queued) {
    fn();
  }
}

/** A container of the given CSS size. 0x0 is the hidden case. */
function fakeContainer(width: number, height: number): HTMLElement {
  return { clientWidth: width, clientHeight: height } as unknown as HTMLElement;
}

function fakeAdapter(dims: TerminalDimensions): Pick<RendererAdapter, "fit"> {
  return { fit: () => dims };
}

let loop: FitLoop | null = null;

beforeEach(() => {
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => {
    frames.push(cb);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {
    frames = [];
  });
});

afterEach(() => {
  loop?.dispose();
  loop = null;
  vi.unstubAllGlobals();
});

describe("fitLoop snapshot", () => {
  it("starts empty and reports a queued fit as pending", () => {
    const sent: TerminalDimensions[] = [];
    loop = createFitLoop({
      getAdapter: () => fakeAdapter({ cols: 100, rows: 40 }),
      getContainer: () => fakeContainer(800, 600),
      onGridChange: (d) => sent.push(d),
    });

    expect(loop.snapshot()).toEqual({ lastSent: null, pending: false, lastSkipReason: null });

    loop.schedule();
    expect(loop.snapshot().pending).toBe(true);

    runFrame();
    expect(loop.snapshot()).toEqual({
      lastSent: { cols: 100, rows: 40 },
      pending: false,
      lastSkipReason: null,
    });
    expect(sent).toEqual([{ cols: 100, rows: 40 }]);
  });

  it("records 'hidden' when the container measures 0x0", () => {
    loop = createFitLoop({
      getAdapter: () => fakeAdapter({ cols: 100, rows: 40 }),
      getContainer: () => fakeContainer(0, 0),
      onGridChange: () => {
        throw new Error("a hidden pane must never resize the remote PTY");
      },
    });

    loop.schedule();
    runFrame();

    expect(loop.snapshot()).toEqual({
      lastSent: null,
      pending: false,
      lastSkipReason: "hidden",
    });
  });

  it("records 'no-adapter' before the card has wired up its renderer", () => {
    loop = createFitLoop({
      getAdapter: () => null,
      getContainer: () => fakeContainer(800, 600),
      onGridChange: () => {},
    });

    loop.schedule();
    runFrame();

    expect(loop.snapshot().lastSkipReason).toBe("no-adapter");
  });

  it("records 'unchanged' for a fit that lands on the same grid", () => {
    const sent: TerminalDimensions[] = [];
    loop = createFitLoop({
      getAdapter: () => fakeAdapter({ cols: 100, rows: 40 }),
      getContainer: () => fakeContainer(800, 600),
      onGridChange: (d) => sent.push(d),
    });

    loop.schedule();
    runFrame();
    loop.schedule();
    runFrame();

    // The second fit computed the same grid, so the remote heard about it once.
    expect(sent).toHaveLength(1);
    expect(loop.snapshot()).toEqual({
      lastSent: { cols: 100, rows: 40 },
      pending: false,
      lastSkipReason: "unchanged",
    });
  });

  it("forgets everything on dispose", () => {
    loop = createFitLoop({
      getAdapter: () => fakeAdapter({ cols: 100, rows: 40 }),
      getContainer: () => fakeContainer(0, 0),
      onGridChange: () => {},
    });

    loop.schedule();
    runFrame();
    loop.schedule(); // leaves a frame queued
    loop.dispose();

    expect(loop.snapshot()).toEqual({ lastSent: null, pending: false, lastSkipReason: null });
  });
});
