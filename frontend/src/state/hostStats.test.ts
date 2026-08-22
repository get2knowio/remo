// The host-stats poll loop: 5s cadence, visibility pause/resume, keep-last-
// snapshot-on-failure, and the 409 `unsupported_host_tools` stop condition.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getHostStats = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  // Lazy closure: the factory is hoisted above the const initialization.
  return { ...actual, getHostStats: (...args: unknown[]) => getHostStats(...args) };
});

import { ApiError } from "../api/client";
import { useHostStats } from "./hostStats";

function snapshot(cpu = 10): Record<string, unknown> {
  return {
    uptime_s: 100,
    load_1: 0.5,
    load_5: 0.4,
    load_15: 0.3,
    cpu_count: 4,
    cpu_used_pct: cpu,
    mem_total: 1024,
    mem_used: 512,
    mem_available: 512,
    swap_total: 0,
    swap_used: 0,
    disks: [],
    temps: [],
  };
}

function setVisibility(state: "visible" | "hidden"): void {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  getHostStats.mockReset();
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useHostStats", () => {
  it("fetches immediately and then every 5s", async () => {
    getHostStats.mockResolvedValue(snapshot());
    const { result, unmount } = renderHook(() => useHostStats("i-1"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getHostStats).toHaveBeenCalledTimes(1);
    expect(result.current.stats).toMatchObject({ cpu_used_pct: 10 });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(getHostStats).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("pauses while hidden and refetches immediately on visible", async () => {
    getHostStats.mockResolvedValue(snapshot());
    const { unmount } = renderHook(() => useHostStats("i-1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getHostStats).toHaveBeenCalledTimes(1);

    act(() => setVisibility("hidden"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    // Four intervals elapsed hidden — none polled.
    expect(getHostStats).toHaveBeenCalledTimes(1);

    await act(async () => {
      setVisibility("visible");
      await vi.advanceTimersByTimeAsync(0);
    });
    // The return-to-tab refetch is immediate, not waiting out the interval.
    expect(getHostStats).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("keeps the last snapshot, flagged stale, when a poll fails", async () => {
    getHostStats.mockResolvedValueOnce(snapshot(42));
    const { result, unmount } = renderHook(() => useHostStats("i-1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.stats).toMatchObject({ cpu_used_pct: 42 });
    expect(result.current.stale).toBe(false);

    getHostStats.mockRejectedValue(
      new ApiError({ code: "bad_gateway", message: "x", retryable: true, remediation: "" }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(result.current.stats).toMatchObject({ cpu_used_pct: 42 }); // kept
    expect(result.current.stale).toBe(true);
    unmount();
  });

  it("stops polling and exposes the nudge on a 409 unsupported_host_tools", async () => {
    getHostStats.mockRejectedValue(
      new ApiError({
        code: "unsupported_host_tools",
        message: "host tools predate stats",
        retryable: false,
        remediation: "Run: remo incus upgrade box",
      }),
    );
    const { result, unmount } = renderHook(() => useHostStats("i-1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.unsupported).toMatchObject({
      code: "unsupported_host_tools",
      remediation: "Run: remo incus upgrade box",
    });

    const calls = getHostStats.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    // Re-asking every 5s can't change a host's installed tools.
    expect(getHostStats).toHaveBeenCalledTimes(calls);
    unmount();
  });
});
