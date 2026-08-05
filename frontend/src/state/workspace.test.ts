import { act, renderHook, type RenderHookResult } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SessionTarget } from "../api/client";
import { MASTER_SIDES, type UseWorkspaceResult } from "./workspace";

const target = (id: string) => ({ id }) as unknown as SessionTarget;

// The store is a module singleton (persisted to localStorage). Reset both per
// test so each starts from a clean slate.
async function mount(): Promise<RenderHookResult<UseWorkspaceResult, unknown>> {
  vi.resetModules();
  window.localStorage.clear();
  const mod = await import("./workspace");
  return renderHook(() => mod.useWorkspace());
}

describe("workspace fullscreen overlay", () => {
  it("maximize is orthogonal: it sets maximizedId without disturbing visible", async () => {
    const { result } = await mount();
    act(() => result.current.selectOnly(target("a")));
    expect(result.current.visible).toEqual(["a"]);

    act(() => result.current.maximize("a"));
    expect(result.current.maximizedId).toBe("a");
    // The single/grid layout underneath is untouched.
    expect(result.current.visible).toEqual(["a"]);

    act(() => result.current.restore());
    expect(result.current.maximizedId).toBeNull();
    expect(result.current.visible).toEqual(["a"]);
  });

  it("closing the maximized terminal clears the overlay", async () => {
    const { result } = await mount();
    act(() => result.current.selectOnly(target("a")));
    act(() => result.current.maximize("a"));
    act(() => result.current.closeTerm("a"));
    expect(result.current.maximizedId).toBeNull();
    expect(result.current.attached).not.toContain("a");
  });

  it("closing a different terminal leaves the overlay intact", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.maximize("a"));
    act(() => result.current.closeTerm("b"));
    expect(result.current.maximizedId).toBe("a");
  });

  it("backToGrid from fullscreen-over-a-grid restores the grid and clears the overlay", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    expect(result.current.visible).toEqual(["a", "b"]);
    act(() => result.current.maximize("a"));
    act(() => result.current.backToGrid());
    expect(result.current.maximizedId).toBeNull();
    expect(result.current.visible).toEqual(["a", "b"]);
  });

  it("an explicit layout change (selectOnly) clears the overlay", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.maximize("a"));
    act(() => result.current.selectOnly(target("b")));
    expect(result.current.maximizedId).toBeNull();
    expect(result.current.visible).toEqual(["b"]);
  });
});

describe("workspace grid reorder (swapVisible)", () => {
  it("swaps two tiles' positions in the grid order", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    expect(result.current.visible).toEqual(["a", "b", "c"]);

    act(() => result.current.swapVisible("a", "c"));
    expect(result.current.visible).toEqual(["c", "b", "a"]);
  });

  it("is a no-op when an id isn't visible or both are the same", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.swapVisible("a", "zzz")); // zzz not visible
    expect(result.current.visible).toEqual(["a", "b"]);
    act(() => result.current.swapVisible("a", "a"));
    expect(result.current.visible).toEqual(["a", "b"]);
  });

  it("rebuilding the grid (open-many) discards a custom order", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.swapVisible("a", "b"));
    expect(result.current.visible).toEqual(["b", "a"]);
    // The grid "goes away" and is rebuilt fresh.
    act(() => result.current.openMany([target("a"), target("b")]));
    expect(result.current.visible).toEqual(["a", "b"]);
  });
});

// --- master/stack tiling ----------------------------------------------------
//
// The invariant lives in one place (normalizeLayout, called from setState), so
// most of these prove that actions which never mention `layout` still leave it
// coherent.

const STORAGE_KEY = "remo-web:workspace";

describe("workspace master layout", () => {
  it("promotes a visible tile to the master area at the default split", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => result.current.setMaster("c", "right"));

    expect(result.current.layout).toEqual({ kind: "master", id: "c", side: "right" });
  });

  it("re-tiles to another edge", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.setMaster("a", "right"));
    act(() => result.current.setMaster("a", "top"));

    expect(result.current.layout).toEqual({ kind: "master", id: "a", side: "top" });
  });

  it("clearMaster returns to the grid without disturbing the tile order", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => result.current.setMaster("b", "left"));
    act(() => result.current.clearMaster());

    expect(result.current.layout).toEqual({ kind: "grid" });
    expect(result.current.visible).toEqual(["a", "b", "c"]);
  });

  it("ignores a master that isn't visible, and one with too few tiles", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));

    act(() => result.current.setMaster("nope", "left"));
    expect(result.current.layout).toEqual({ kind: "grid" });

    act(() => result.current.selectOnly(target("a")));
    act(() => result.current.setMaster("a", "left"));
    expect(result.current.layout).toEqual({ kind: "grid" });
  });

  // 4.1.0 persisted the split inside the layout. It moved to settings.masterSplit,
  // so the stale key must simply be ignored rather than needing a migration.
  it("loads a 4.1.0 layout, dropping its embedded split", async () => {
    window.localStorage.clear();
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        attached: ["a", "b"],
        visible: ["a", "b"],
        focusedId: "a",
        layout: { kind: "master", id: "a", side: "left", fraction: 0.99 },
      }),
    );
    vi.resetModules();
    const mod = await import("./workspace");
    const { result } = renderHook(() => mod.useWorkspace());
    expect(result.current.layout).toEqual({ kind: "master", id: "a", side: "left" });
  });

  // Closing the master moves ONE tile instead of reflowing every survivor.
  it("promotes the head of the stack when the master is closed", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => result.current.setMaster("c", "right"));
    act(() => result.current.closeTerm("c"));

    expect(result.current.layout).toMatchObject({ kind: "master", id: "a", side: "right" });
    expect(result.current.visible).toEqual(["a", "b"]);
  });

  it("falls back to the grid once too few tiles remain", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.setMaster("a", "right"));
    act(() => result.current.closeTerm("b"));

    expect(result.current.layout).toEqual({ kind: "grid" });
  });

  it("survives solo and comes back with the grid", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => result.current.setMaster("c", "top"));
    // Escape routes here — the tiling must not be destroyed by it.
    act(() => result.current.soloTile("a"));
    expect(result.current.layout).toEqual({ kind: "grid" });

    act(() => result.current.backToGrid());
    expect(result.current.layout).toMatchObject({ kind: "master", id: "c", side: "top" });
  });

  it("is discarded by an open-many rebuild", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.setMaster("a", "left"));
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));

    expect(result.current.layout).toEqual({ kind: "grid" });
  });

  // Mastership is attached to the SLOT: dragging the master onto a stack tile
  // must actually move the dragged tile.
  it("transfers mastership when the master is swapped", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => result.current.setMaster("a", "right"));

    act(() => result.current.swapVisible("a", "c"));
    expect(result.current.layout).toMatchObject({ id: "c" });

    act(() => result.current.swapVisible("b", "c"));
    expect(result.current.layout).toMatchObject({ id: "b" });
  });

  it("leaves the master alone when neither swapped tile holds it", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => result.current.setMaster("a", "right"));
    act(() => result.current.swapVisible("b", "c"));

    expect(result.current.layout).toMatchObject({ id: "a" });
  });

  it("is orthogonal to fullscreen", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.setMaster("b", "bottom"));

    act(() => result.current.maximize("a"));
    expect(result.current.layout).toMatchObject({ kind: "master", id: "b" });
    act(() => result.current.restore());
    expect(result.current.layout).toMatchObject({ kind: "master", id: "b" });
  });
});

describe("workspace master layout persistence", () => {
  it("round-trips through localStorage", async () => {
    const first = await mount();
    act(() => first.result.current.openMany([target("a"), target("b"), target("c")]));
    act(() => first.result.current.setMaster("b", "left"));

    vi.resetModules();
    const mod = await import("./workspace");
    const { result } = renderHook(() => mod.useWorkspace());
    expect(result.current.layout).toMatchObject({ kind: "master", id: "b", side: "left" });
  });

  it("does not persist the remembered tiling", async () => {
    const { result } = await mount();
    act(() => result.current.openMany([target("a"), target("b")]));
    act(() => result.current.setMaster("a", "left"));
    act(() => result.current.soloTile("a"));

    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, unknown>;
    expect(Object.keys(stored).sort()).toEqual(["attached", "focusedId", "layout", "visible"]);
  });

  it.each([
    ["a stale master id", { kind: "master", id: "gone", side: "left", fraction: 0.6 }],
    ["a bad side", { kind: "master", id: "a", side: "sideways", fraction: 0.6 }],
    ["a stale embedded split from 4.1.0", { kind: "master", id: "a", side: "left", fraction: "wide" }],
    ["an unknown kind from a newer build", { kind: "columns", id: "a" }],
    ["a non-object", "master"],
    ["an array", []],
  ])("loads sanely with %s", async (_label, layout) => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ attached: ["a", "b"], visible: ["a", "b"], focusedId: "a", layout }),
    );
    vi.resetModules();
    const mod = await import("./workspace");
    const { result } = renderHook(() => mod.useWorkspace());

    // Either a coherent master on a real tile, or the grid. Never a dangling id.
    const restored = result.current.layout;
    if (restored.kind === "master") {
      expect(result.current.visible).toContain(restored.id);
      expect(MASTER_SIDES).toContain(restored.side);
    } else {
      expect(restored).toEqual({ kind: "grid" });
    }
  });
});
