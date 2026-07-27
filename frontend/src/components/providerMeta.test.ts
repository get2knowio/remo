import { describe, expect, it } from "vitest";
import type { InstanceStatus } from "../api/client";
import { providerMeta, statusMeta } from "./providerMeta";

describe("statusMeta", () => {
  it("does not throw and falls back gracefully for a status outside the compiled union", () => {
    // Simulates an old browser bundle talking to a newer service that added a
    // status value never seen at build time (SC-010, FR-013a). The cast is
    // required because TypeScript won't otherwise let an invalid literal
    // through statusMeta's InstanceStatus parameter.
    const offUnionStatus = "degraded" as InstanceStatus;

    expect(() => statusMeta(offUnionStatus)).not.toThrow();

    const result = statusMeta(offUnionStatus);
    expect(result).toEqual({
      label: "degraded",
      color: "var(--text-dim)",
      pulse: false,
    });
  });

  it("still maps a known status to its dedicated presentation", () => {
    expect(statusMeta("ok")).toEqual({ label: "online", color: "var(--ok)", pulse: false });
  });
});

describe("providerMeta", () => {
  it("falls back gracefully for an unrecognized/third-party provider type", () => {
    // A third-party provider's instance_type is genuinely valid wire data
    // (SC-009) — the console must render it, not crash.
    expect(providerMeta("vultr")).toEqual({
      label: "vultr",
      color: "var(--prov-unknown)",
    });
  });

  it("still maps a known provider to its dedicated presentation", () => {
    expect(providerMeta("aws")).toEqual({ label: "AWS", color: "var(--prov-aws)" });
  });
});
