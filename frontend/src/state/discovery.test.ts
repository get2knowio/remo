// The background poll has to TRIGGER discovery, not just re-read its result.
//
// `GET /hosts` and `GET /sessions` are cache reads on the service side — only
// `POST /discovery/refresh` re-runs discovery. When the interval tick called
// nothing but the two GETs, the console re-rendered the identical snapshot
// forever: a Zellij session started after page load never lit its ⚡, git
// ahead/behind counts never moved, and a newly-created project never appeared,
// until the user reloaded the whole page.
//
// These tests pin the fix at the seam that broke: what the tick actually sends.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const refreshDiscovery = vi.fn(async () => ({ refreshing: true }));
const getHosts = vi.fn(async () => ({ instances: [] }));
const getSessions = vi.fn(async () => ({ targets: [] }));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    refreshDiscovery,
    getHosts,
    getSessions,
  };
});

const INTERVAL_MS = 15_000;

/** Advance timers inside `act` so the store's async state updates settle. */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("discovery auto-refresh", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    refreshDiscovery.mockClear();
    getHosts.mockClear();
    getSessions.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("mounts with a forced run, then ticks trigger TTL-gated ones", async () => {
    const { useDiscovery } = await import("./discovery");
    const { unmount } = renderHook(() => useDiscovery(INTERVAL_MS));

    // Mount: a forced run, so a cold cache is populated without the user
    // having to click Refresh.
    await advance(0);
    expect(refreshDiscovery).toHaveBeenCalledTimes(1);
    expect(refreshDiscovery.mock.calls[0]).toEqual([undefined]);

    // Let the mount refresh finish its follow-up polls before judging ticks.
    await advance(10_000);
    refreshDiscovery.mockClear();

    // The regression: a tick used to trigger no discovery at all, so the cache
    // it then read back was the same one page load produced — forever.
    await advance(INTERVAL_MS + 1);
    expect(refreshDiscovery).toHaveBeenCalled();
    expect(refreshDiscovery.mock.calls[0]).toEqual([undefined, { force: false }]);

    unmount();
  });

  it("a tick reads the cache after triggering the run, not before", async () => {
    const { useDiscovery } = await import("./discovery");
    const { unmount } = renderHook(() => useDiscovery(INTERVAL_MS));
    await advance(10_000);
    refreshDiscovery.mockClear();
    getHosts.mockClear();

    await advance(INTERVAL_MS + 1);
    // The tick awaits the POST before polling, so let that continuation land.
    await advance(100);

    expect(refreshDiscovery).toHaveBeenCalled();
    expect(getHosts).toHaveBeenCalled();
    expect(refreshDiscovery.mock.invocationCallOrder[0]).toBeLessThan(
      getHosts.mock.invocationCallOrder[0],
    );

    unmount();
  });

  it("stops triggering discovery once the last subscriber unmounts", async () => {
    const { useDiscovery } = await import("./discovery");
    const { unmount } = renderHook(() => useDiscovery(INTERVAL_MS));
    await advance(10_000);

    unmount();
    refreshDiscovery.mockClear();
    await advance(INTERVAL_MS * 3);

    expect(refreshDiscovery).not.toHaveBeenCalled();
  });
});
