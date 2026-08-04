// Pane geometry for the workspace grid — pure, DOM-free, and unit-tested.
//
// Lives outside WorkspacePane for the same reason `themeMenuPosition` lives
// outside its popup: every rendered tile boots a terminal adapter, so the
// component is expensive to exercise, while the geometry is the part most worth
// pinning. jsdom has no layout engine either, so anything measured through the
// DOM would be untestable — everything here takes its inputs explicitly.
//
// Two arrangements are expressed:
//   - the uniform grid, unchanged from what the console has always rendered;
//   - master/stack (dwm/xmonad), where one tile takes `fraction` of one side and
//     the rest tile the remainder.
//
// Both are emitted as a `grid-template` for the container plus an explicit
// `grid-area` per visible tile. The areas matter: CSS auto-placement flowing
// around an explicitly-placed spanning item is order- and direction-sensitive,
// so it works until someone reorders the JSX. Placing every tile is one extra
// map entry and is fully assertable.

import type { CSSProperties } from "react";
import type { MasterSide, WorkspaceLayout } from "../state/workspace";

export interface PaneLayout {
  /** Inline grid template for `.workspace-body`. */
  container: CSSProperties;
  /** id -> `grid-area`. EMPTY for the uniform grid, so tiles fall back to
   * auto-placement and the rendered output is byte-identical to before this
   * module existed. */
  areaById: Map<string, string>;
}

/** Columns for the uniform grid. Moved verbatim from WorkspacePane so the
 * existing layout path gets test coverage too. */
export function gridColumns(visibleCount: number, narrow: boolean): number {
  if (narrow) {
    return 1;
  }
  if (visibleCount >= 5) {
    return 3;
  }
  if (visibleCount >= 2) {
    return 2;
  }
  return 1;
}

/** Past four, the stack goes two-wide.
 *
 * A vertical stack of eight in a ~200px-tall pane gives ~25px tiles. That is
 * ABOVE TerminalCard's 0x0 guard, so it would happily fit() to a single row and
 * send that to the remote PTY, corrupting a TUI. Widening the stack keeps every
 * tile at least as big as it would be in the plain grid. */
function stackColumns(stackCount: number): number {
  return stackCount >= 5 ? 2 : 1;
}

const track = (n: number): string => `minmax(0, ${n}fr)`;
const evenTracks = (n: number): string => `repeat(${n}, minmax(0, 1fr))`;

/** Split a fraction into two whole `fr` units.
 *
 * Whole numbers on a 100 basis, because `1 - 0.7` is 0.30000000000000004 in
 * float64 and that would leak into the style string (and into every assertion
 * about it). `fr` distributes free space AFTER gaps, so the existing 10px gap is
 * unaffected. */
function split(fraction: number): [number, number] {
  const master = Math.round(fraction * 100);
  return [master, 100 - master];
}

/** `grid-area` shorthand: row-start / col-start / row-end / col-end. */
const area = (r1: number, c1: number, r2: number, c2: number): string =>
  `${r1} / ${c1} / ${r2} / ${c2}`;

export function paneLayout(
  layout: WorkspaceLayout,
  visible: string[],
  narrow: boolean,
  /** The master's share of the pane (settings.masterSplit). */
  fraction: number,
): PaneLayout {
  if (layout.kind === "master" && visible.includes(layout.id) && visible.length > 1) {
    return masterLayout(layout.id, layout.side, fraction, visible);
  }
  const cols = gridColumns(visible.length, narrow);
  const rows = Math.max(1, Math.ceil(visible.length / cols));
  return {
    container: { gridTemplateColumns: evenTracks(cols), gridTemplateRows: evenTracks(rows) },
    areaById: new Map(),
  };
}

function masterLayout(
  masterId: string,
  side: MasterSide,
  fraction: number,
  visible: string[],
): PaneLayout {
  const stack = visible.filter((id) => id !== masterId);
  const [masterFr, stackFr] = split(fraction);
  const horizontal = side === "left" || side === "right";
  const masterFirst = side === "left" || side === "top";

  // How the stack subdivides ACROSS the master's axis (1 or 2), and how deep it
  // runs ALONG it. For a left/right master the stack subdivides into columns and
  // runs down rows; for top/bottom it is transposed.
  const across = stackColumns(stack.length);
  const depth = Math.max(1, Math.ceil(stack.length / across));

  // Scale the master track by `across` so every track stays a whole number: with
  // a 2-wide stack, 60/40 becomes 120fr against 2x40fr, which is the same ratio
  // without ever emitting something like 22.5fr.
  const masterTrack = track(masterFr * across);
  const stackTracks = `repeat(${across}, ${track(stackFr)})`;
  const alongTracks = evenTracks(depth);
  const masterAxis = masterFirst ? `${masterTrack} ${stackTracks}` : `${stackTracks} ${masterTrack}`;

  // Where each region starts on the master's axis (1-based grid lines).
  const masterLine = masterFirst ? 1 : across + 1;
  const stackLine = masterFirst ? 2 : 1;

  // A stack that doesn't divide evenly would otherwise leave an empty cell in
  // its last row — the exact hole this feature exists to remove from the uniform
  // grid. The final tile spans the remainder instead.
  const partial = stack.length % across !== 0;
  const spanEnd = (k: number, line: number): number =>
    partial && k === stack.length - 1 ? line + across : line + (k % across) + 1;

  const areaById = new Map<string, string>();
  if (horizontal) {
    areaById.set(masterId, area(1, masterLine, depth + 1, masterLine + 1));
    stack.forEach((id, k) => {
      const row = Math.floor(k / across) + 1;
      const col = stackLine + (k % across);
      areaById.set(id, area(row, col, row + 1, spanEnd(k, stackLine)));
    });
    return {
      container: { gridTemplateColumns: masterAxis, gridTemplateRows: alongTracks },
      areaById,
    };
  }

  areaById.set(masterId, area(masterLine, 1, masterLine + 1, depth + 1));
  stack.forEach((id, k) => {
    const col = Math.floor(k / across) + 1;
    const row = stackLine + (k % across);
    areaById.set(id, area(row, col, spanEnd(k, stackLine), col + 1));
  });
  return {
    container: { gridTemplateColumns: alongTracks, gridTemplateRows: masterAxis },
    areaById,
  };
}

// --- drag/drop and the cycle button ----------------------------------------

export type DropIntent =
  | { kind: "master"; id: string; side: MasterSide }
  | { kind: "swap"; a: string; b: string }
  | null;

/** What a drop means. Pure so it can be tested: jsdom reports every element as
 * 0x0, so a real dnd-kit drop cannot be simulated and this branch would
 * otherwise have no coverage at all. */
export function dropIntent(
  activeId: string,
  over: { id: string; snapSide?: MasterSide } | null,
): DropIntent {
  if (!over) {
    return null;
  }
  if (over.snapSide) {
    return { kind: "master", id: activeId, side: over.snapSide };
  }
  if (over.id === activeId) {
    return null;
  }
  return { kind: "swap", a: activeId, b: over.id };
}

/** Order the tile button cycles through. Drawn by the button's glyphs. */
const MASTER_CYCLE: MasterSide[] = ["left", "top", "right", "bottom"];

/** The next side for `id`, or null to return to the uniform grid. Taking
 * mastership from another tile restarts the cycle, so the button always
 * advances visibly rather than appearing to do nothing. */
export function nextMasterSide(layout: WorkspaceLayout, id: string): MasterSide | null {
  if (layout.kind !== "master" || layout.id !== id) {
    return MASTER_CYCLE[0];
  }
  return MASTER_CYCLE[MASTER_CYCLE.indexOf(layout.side) + 1] ?? null;
}
