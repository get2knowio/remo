import { describe, expect, it } from "vitest";
import type { WorkspaceLayout } from "../state/workspace";
import { dropIntent, gridColumns, nextMasterSide, paneLayout } from "./masterLayout";

const grid: WorkspaceLayout = { kind: "grid" };
const master = (id: string, side: "left" | "right" | "top" | "bottom"): WorkspaceLayout => ({
  kind: "master",
  id,
  side,
});
/** The default split (settings.masterSplit), as stack/master = 40/60. */
const SPLIT = 0.6;

/** Every grid line an area occupies, so overlaps and holes are detectable. */
function cellsOf(areaSpec: string): string[] {
  const [r1, c1, r2, c2] = areaSpec.split(" / ").map(Number);
  const cells: string[] = [];
  for (let r = r1; r < r2; r += 1) {
    for (let c = c1; c < c2; c += 1) {
      cells.push(`${r},${c}`);
    }
  }
  return cells;
}

function trackCount(template: string): number {
  const repeat = /repeat\((\d+),/.exec(template);
  const repeats = repeat ? Number(repeat[1]) : 0;
  const singles = (template.match(/minmax\(0, \d+fr\)/g) ?? []).length - (repeat ? 1 : 0);
  return repeats + Math.max(0, singles);
}

describe("uniform grid", () => {
  // The degradation guarantee: with no master set, this module must reproduce
  // exactly what WorkspacePane rendered before it existed.
  it.each([
    [1, false, 1, 1],
    [2, false, 2, 1],
    [3, false, 2, 2],
    [4, false, 2, 2],
    [5, false, 3, 2],
    [9, false, 3, 3],
    [4, true, 1, 4],
  ])("%i tiles (narrow=%s) -> %i cols x %i rows", (count, narrow, cols, rows) => {
    const visible = Array.from({ length: count }, (_, i) => `t${i}`);
    const { container, areaById } = paneLayout(grid, visible, narrow, SPLIT);
    expect(container.gridTemplateColumns).toBe(`repeat(${cols}, minmax(0, 1fr))`);
    expect(container.gridTemplateRows).toBe(`repeat(${rows}, minmax(0, 1fr))`);
    // No explicit areas: tiles auto-place exactly as they always have.
    expect(areaById.size).toBe(0);
  });

  it("matches gridColumns, which moved here from WorkspacePane", () => {
    expect(gridColumns(1, false)).toBe(1);
    expect(gridColumns(4, false)).toBe(2);
    expect(gridColumns(5, false)).toBe(3);
    expect(gridColumns(9, true)).toBe(1);
  });
});

describe("master/stack", () => {
  // The motivating case: 3 terminals, one filling the right side full-height.
  it("puts the master on the right at 60% with the stack down the left", () => {
    const { container, areaById } = paneLayout(master("c", "right"), ["a", "b", "c"], false, SPLIT);
    expect(container.gridTemplateColumns).toBe("repeat(1, minmax(0, 40fr)) minmax(0, 60fr)");
    expect(container.gridTemplateRows).toBe("repeat(2, minmax(0, 1fr))");
    expect(areaById.get("c")).toBe("1 / 2 / 3 / 3"); // full height, right column
    expect(areaById.get("a")).toBe("1 / 1 / 2 / 2");
    expect(areaById.get("b")).toBe("2 / 1 / 3 / 2");
  });

  it("mirrors for a left master", () => {
    const { container, areaById } = paneLayout(master("c", "left"), ["a", "b", "c"], false, SPLIT);
    expect(container.gridTemplateColumns).toBe("minmax(0, 60fr) repeat(1, minmax(0, 40fr))");
    expect(areaById.get("c")).toBe("1 / 1 / 3 / 2");
    expect(areaById.get("a")).toBe("1 / 2 / 2 / 3");
    expect(areaById.get("b")).toBe("2 / 2 / 3 / 3");
  });

  it("transposes for a top master — stack tiles horizontally below", () => {
    const { container, areaById } = paneLayout(master("c", "top"), ["a", "b", "c"], false, SPLIT);
    expect(container.gridTemplateRows).toBe("minmax(0, 60fr) repeat(1, minmax(0, 40fr))");
    expect(container.gridTemplateColumns).toBe("repeat(2, minmax(0, 1fr))");
    expect(areaById.get("c")).toBe("1 / 1 / 2 / 3"); // full width, top row
    expect(areaById.get("a")).toBe("2 / 1 / 3 / 2");
    expect(areaById.get("b")).toBe("2 / 2 / 3 / 3");
  });

  it("transposes for a bottom master", () => {
    const { container, areaById } = paneLayout(master("c", "bottom"), ["a", "b", "c"], false, SPLIT);
    expect(container.gridTemplateRows).toBe("repeat(1, minmax(0, 40fr)) minmax(0, 60fr)");
    expect(areaById.get("c")).toBe("2 / 1 / 3 / 3");
    expect(areaById.get("a")).toBe("1 / 1 / 2 / 2");
  });

  it("handles a single stack tile (two visible)", () => {
    const { container, areaById } = paneLayout(master("a", "right"), ["a", "b"], false, SPLIT);
    expect(container.gridTemplateRows).toBe("repeat(1, minmax(0, 1fr))");
    expect(areaById.get("a")).toBe("1 / 2 / 2 / 3");
    expect(areaById.get("b")).toBe("1 / 1 / 2 / 2");
  });

  it("follows visible order, skipping the master", () => {
    const { areaById } = paneLayout(master("b", "right"), ["a", "b", "c", "d"], false, SPLIT);
    // a, c, d keep their relative order down the stack.
    expect(areaById.get("a")).toBe("1 / 1 / 2 / 2");
    expect(areaById.get("c")).toBe("2 / 1 / 3 / 2");
    expect(areaById.get("d")).toBe("3 / 1 / 4 / 2");
  });

  // Past four, a 1-wide stack would give ~25px tiles on an iPad — above the
  // TerminalCard 0x0 guard, so it would fit() to one row and corrupt a TUI.
  it("widens the stack to two tracks past four stack tiles", () => {
    const visible = ["m", "a", "b", "c", "d", "e"];
    const { container, areaById } = paneLayout(master("m", "right"), visible, false, SPLIT);
    expect(container.gridTemplateColumns).toBe("repeat(2, minmax(0, 40fr)) minmax(0, 120fr)");
    expect(container.gridTemplateRows).toBe("repeat(3, minmax(0, 1fr))");
    expect(areaById.get("m")).toBe("1 / 3 / 4 / 4");
    expect(areaById.get("a")).toBe("1 / 1 / 2 / 2");
    expect(areaById.get("b")).toBe("1 / 2 / 2 / 3");
    expect(areaById.get("c")).toBe("2 / 1 / 3 / 2");
  });

  // The master track is scaled by the stack width so the ratio survives without
  // ever emitting a fractional fr.
  it.each([0.3, 0.5, 0.55, 0.6, 0.7, 0.75])(
    "emits whole fr units at fraction %s",
    (fraction) => {
      for (const side of ["left", "right", "top", "bottom"] as const) {
        for (const count of [2, 3, 6, 9]) {
          const visible = Array.from({ length: count }, (_, i) => `t${i}`);
          const { container } = paneLayout(master("t0", side), visible, false, fraction);
          const template = `${container.gridTemplateColumns} ${container.gridTemplateRows}`;
          expect(template, `${side} @ ${fraction} x${count}`).not.toMatch(/\d\.\d/);
        }
      }
    },
  );

  it("tiles the pane exactly — every cell filled once, no overlap", () => {
    for (const side of ["left", "right", "top", "bottom"] as const) {
      for (const count of [2, 3, 4, 6, 9]) {
        const visible = Array.from({ length: count }, (_, i) => `t${i}`);
        const { container, areaById } = paneLayout(master("t0", side), visible, false, SPLIT);
        const cols = trackCount(String(container.gridTemplateColumns));
        const rows = trackCount(String(container.gridTemplateRows));
        const seen = new Set<string>();
        let total = 0;
        for (const spec of areaById.values()) {
          for (const cell of cellsOf(spec)) {
            expect(seen.has(cell), `${side} x${count}: ${cell} covered twice`).toBe(false);
            seen.add(cell);
            total += 1;
          }
        }
        expect(total, `${side} x${count}`).toBe(rows * cols);
        expect(areaById.size).toBe(count);
      }
    }
  });

  it("falls back to the uniform grid when the master isn't visible", () => {
    const { areaById } = paneLayout(master("gone", "right"), ["a", "b"], false, SPLIT);
    expect(areaById.size).toBe(0);
  });
});

describe("dropIntent", () => {
  it("treats an edge zone as a tiling request, beating the swap", () => {
    expect(dropIntent("a", { id: "remo-snap:right", snapSide: "right" })).toEqual({
      kind: "master",
      id: "a",
      side: "right",
    });
  });

  it("falls back to a swap over another tile", () => {
    expect(dropIntent("a", { id: "b" })).toEqual({ kind: "swap", a: "a", b: "b" });
  });

  it("does nothing for a self-drop or no drop target", () => {
    expect(dropIntent("a", { id: "a" })).toBeNull();
    expect(dropIntent("a", null)).toBeNull();
  });
});

describe("nextMasterSide", () => {
  it("cycles left -> top -> right -> bottom -> grid", () => {
    let layout: WorkspaceLayout = grid;
    const seen: (string | null)[] = [];
    for (let i = 0; i < 5; i += 1) {
      const side = nextMasterSide(layout, "a");
      seen.push(side);
      layout = side ? master("a", side) : grid;
    }
    expect(seen).toEqual(["left", "top", "right", "bottom", null]);
  });

  it("restarts the cycle when taking mastership from another tile", () => {
    expect(nextMasterSide(master("b", "bottom"), "a")).toBe("left");
  });
});
