import { act, renderHook, type RenderHookResult } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SessionTarget } from "../api/client";
import type { UseWorkspaceResult } from "./workspace";

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
