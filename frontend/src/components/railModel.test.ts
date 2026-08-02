// The rail's ⚡ has two sources of truth, and they have to agree.
//
// `zellij_state` comes from discovery, which is a periodic snapshot: a session
// started from this console isn't in it until the next run lands, so a
// just-launched devcontainer showed no ⚡ until the page was reloaded. But the
// console holds an open terminal on that project, and `remo-host sessions
// attach` creates-or-attaches a Zellij session — so a live terminal IS an
// active session, knowable immediately.
//
// Both feed `RailRow.active`, which drives the ⚡ *and* the "Active only"
// filter, so a row can never show a bolt while being filtered out as inactive.

import { describe, expect, it } from "vitest";
import type { DiscoveryInstance, SessionTarget } from "../api/client";
import { buildRailModel, type RailFilters } from "./railModel";

const NO_FILTERS: RailFilters = { search: "", providerFilter: null, sessionOnly: false };

function instance(overrides: Partial<DiscoveryInstance> = {}): DiscoveryInstance {
  return {
    instance_id: "inst-1",
    instance_type: "incus",
    instance_name: "lab/dev1",
    status: "ok",
    region: "",
    capability: null,
    targets: [],
    error: null,
    refreshed_at: "2026-08-02T00:00:00Z",
    ...overrides,
  } as DiscoveryInstance;
}

function target(overrides: Partial<SessionTarget> = {}): SessionTarget {
  return {
    id: "t-1",
    instance_type: "incus",
    instance_name: "lab/dev1",
    project: "remo",
    zellij_state: "absent",
    devcontainer_running: "no",
    git_dirty: false,
    git_ahead: 0,
    git_behind: 0,
    ...overrides,
  } as SessionTarget;
}

function rowsOf(
  targets: SessionTarget[],
  filters: RailFilters = NO_FILTERS,
  live: ReadonlySet<string> = new Set(),
) {
  return buildRailModel([instance()], targets, filters, live).groups[0]?.rows ?? [];
}

describe("RailRow.active", () => {
  it("is true when discovery reports an active Zellij session", () => {
    const rows = rowsOf([target({ zellij_state: "active" })]);

    expect(rows[0].active).toBe(true);
  });

  it("is false for a target with neither a discovered nor a live session", () => {
    const rows = rowsOf([target()]);

    expect(rows[0].active).toBe(false);
  });

  it("is true for a target this console holds a terminal on, before discovery catches up", () => {
    // The reported bug: launching a devcontainer left zellij_state stale at
    // "absent" until a full page reload, so no ⚡ appeared.
    const rows = rowsOf([target({ zellij_state: "absent" })], NO_FILTERS, new Set(["t-1"]));

    expect(rows[0].active).toBe(true);
  });

  it("only marks the target that is actually live", () => {
    const rows = rowsOf(
      [target({ id: "t-1" }), target({ id: "t-2", project: "other" })],
      NO_FILTERS,
      new Set(["t-1"]),
    );

    expect(rows.map((r) => [r.target.id, r.active])).toEqual([
      ["t-1", true],
      ["t-2", false],
    ]);
  });

  it("defaults to discovery alone when no live set is supplied", () => {
    const model = buildRailModel([instance()], [target({ zellij_state: "active" })], NO_FILTERS);

    expect(model.groups[0].rows[0].active).toBe(true);
  });
});

describe('"Active only" filter', () => {
  const sessionOnly: RailFilters = { ...NO_FILTERS, sessionOnly: true };

  it("keeps a discovered-active target", () => {
    const rows = rowsOf([target({ zellij_state: "active" })], sessionOnly);

    expect(rows).toHaveLength(1);
  });

  it("drops a target with no session at all", () => {
    const rows = rowsOf([target()], sessionOnly);

    expect(rows).toHaveLength(0);
  });

  it("keeps a live-terminal target discovery still calls inactive", () => {
    // If the filter used zellij_state while the ⚡ used `active`, this row
    // would vanish from the filtered rail while claiming to have a session.
    const rows = rowsOf([target()], sessionOnly, new Set(["t-1"]));

    expect(rows).toHaveLength(1);
    expect(rows[0].active).toBe(true);
  });
});
