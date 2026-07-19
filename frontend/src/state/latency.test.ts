import { beforeEach, describe, expect, it } from "vitest";
import { getLatencySnapshot, removeLatency, reportLatency } from "./latency";

describe("latency store", () => {
  beforeEach(() => {
    // Clear any samples left by a prior test (module singleton).
    for (const id of ["a", "b", "c", "d"]) {
      removeLatency(id);
    }
  });

  it("reports the median across connected terminals", () => {
    reportLatency("a", 40);
    reportLatency("b", 80);
    reportLatency("c", 120);
    const snap = getLatencySnapshot();
    expect(snap.rttMs).toBe(80);
    expect(snap.count).toBe(3);
  });

  it("updates a terminal's latest sample in place", () => {
    reportLatency("a", 40);
    reportLatency("a", 300);
    expect(getLatencySnapshot()).toEqual({ rttMs: 300, count: 1 });
  });

  it("drops a terminal's sample on removal; null when none remain", () => {
    reportLatency("a", 40);
    reportLatency("b", 80);
    removeLatency("a");
    expect(getLatencySnapshot()).toEqual({ rttMs: 80, count: 1 });
    removeLatency("b");
    expect(getLatencySnapshot()).toEqual({ rttMs: null, count: 0 });
  });
});
