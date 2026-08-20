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

describe("host collapse", () => {
  it("defaults to expanded when no prefs are supplied", () => {
    const model = buildRailModel([instance()], [target()], NO_FILTERS);

    expect(model.groups[0].collapsed).toBe(false);
  });

  it("reflects the stored collapse pref, keeping rows populated for the badge", () => {
    const model = buildRailModel([instance()], [target()], NO_FILTERS, new Set(), {
      collapsedHostIds: new Set(["inst-1"]),
    });

    expect(model.groups[0].collapsed).toBe(true);
    // The component decides what to hide; the model keeps the rows so the
    // collapsed count badge (and a later expand) need no rebuild.
    expect(model.groups[0].rows).toHaveLength(1);
  });

  it("is forced open by an active search, without touching the stored pref", () => {
    const stored = new Set(["inst-1"]);
    const model = buildRailModel(
      [instance()],
      [target()],
      { ...NO_FILTERS, search: "remo" },
      new Set(),
      { collapsedHostIds: stored },
    );

    expect(model.groups[0].collapsed).toBe(false);
    expect(stored.has("inst-1")).toBe(true);
  });

  it("is NOT overridden by the provider or active-only filters", () => {
    const providerOnly = buildRailModel(
      [instance()],
      [target({ zellij_state: "active" })],
      { ...NO_FILTERS, providerFilter: "incus" },
      new Set(),
      { collapsedHostIds: new Set(["inst-1"]) },
    );
    const activeOnly = buildRailModel(
      [instance()],
      [target({ zellij_state: "active" })],
      { ...NO_FILTERS, sessionOnly: true },
      new Set(),
      { collapsedHostIds: new Set(["inst-1"]) },
    );

    expect(providerOnly.groups[0].collapsed).toBe(true);
    expect(activeOnly.groups[0].collapsed).toBe(true);
  });
});

describe("favorites", () => {
  const entry = { project: "remo", instanceType: "incus", instanceName: "lab/dev1" };

  it("resolves a live favorite to its target, carrying the twin row's number", () => {
    const model = buildRailModel([instance()], [target()], NO_FILTERS, new Set(), {
      favorites: { "t-1": entry },
    });

    expect(model.favorites).toHaveLength(1);
    expect(model.favorites[0].target).toEqual(model.groups[0].rows[0].target);
    expect(model.favorites[0].num).toBe(model.groups[0].rows[0].num);
    expect(model.favorites[0].num).toBe(1);
    expect(model.groups[0].rows[0].favorited).toBe(true);
  });

  it("renders a favorite whose target is gone as stale, from the stored entry", () => {
    const model = buildRailModel([instance()], [], NO_FILTERS, new Set(), {
      favorites: { "t-1": entry },
    });

    expect(model.favorites).toHaveLength(1);
    expect(model.favorites[0]).toMatchObject({
      id: "t-1",
      target: null,
      num: null,
      active: false,
      project: "remo",
      hostLabel: "lab/dev1",
    });
  });

  it("keeps the target but gets no number on a non-ok host", () => {
    const model = buildRailModel(
      [instance({ status: "unreachable" })],
      [target()],
      NO_FILTERS,
      new Set(),
      { favorites: { "t-1": entry } },
    );

    expect(model.favorites[0].target).not.toBeNull();
    expect(model.favorites[0].num).toBeNull();
  });

  it("honors all three rail filters, via the stored entry when stale", () => {
    const favorites = { "t-1": entry };

    const provider = buildRailModel([], [], { ...NO_FILTERS, providerFilter: "aws" }, new Set(), {
      favorites,
    });
    expect(provider.favorites).toHaveLength(0);

    const activeOnly = buildRailModel([], [], { ...NO_FILTERS, sessionOnly: true }, new Set(), {
      favorites,
    });
    expect(activeOnly.favorites).toHaveLength(0);

    const searchMiss = buildRailModel([], [], { ...NO_FILTERS, search: "zzz" }, new Set(), {
      favorites,
    });
    expect(searchMiss.favorites).toHaveLength(0);

    const searchHit = buildRailModel([], [], { ...NO_FILTERS, search: "remo" }, new Set(), {
      favorites,
    });
    expect(searchHit.favorites).toHaveLength(1);
  });

  it("keeps a live favorite under 'active only' when this console holds its terminal", () => {
    const model = buildRailModel(
      [instance()],
      [target()],
      { ...NO_FILTERS, sessionOnly: true },
      new Set(["t-1"]),
      { favorites: { "t-1": entry } },
    );

    expect(model.favorites).toHaveLength(1);
    expect(model.favorites[0].active).toBe(true);
  });

  it("sorts by project, then host label", () => {
    const model = buildRailModel([], [], NO_FILTERS, new Set(), {
      favorites: {
        "t-3": { project: "beta", instanceType: "incus", instanceName: "b-host" },
        "t-1": { project: "beta", instanceType: "incus", instanceName: "a-host" },
        "t-2": { project: "alpha", instanceType: "incus", instanceName: "z-host" },
      },
    });

    expect(model.favorites.map((f) => [f.project, f.hostLabel])).toEqual([
      ["alpha", "z-host"],
      ["beta", "a-host"],
      ["beta", "b-host"],
    ]);
  });
});

describe("collapse and favorites never disturb numbering", () => {
  it("leaves flatOpenable, availCount, and every row's num byte-identical", () => {
    const instances = [
      instance(),
      instance({ instance_id: "inst-2", instance_name: "lab/dev2" }),
    ];
    const targets = [
      target({ id: "t-1", project: "alpha" }),
      target({ id: "t-2", project: "beta" }),
      target({ id: "t-3", project: "gamma", instance_name: "lab/dev2" }),
    ];

    const plain = buildRailModel(instances, targets, NO_FILTERS);
    const decorated = buildRailModel(instances, targets, NO_FILTERS, new Set(), {
      collapsedHostIds: new Set(["inst-1", "inst-2"]),
      favorites: {
        "t-2": { project: "beta", instanceType: "incus", instanceName: "lab/dev1" },
        "t-3": { project: "gamma", instanceType: "incus", instanceName: "lab/dev2" },
      },
    });

    expect(decorated.flatOpenable).toEqual(plain.flatOpenable);
    expect(decorated.availCount).toBe(plain.availCount);
    expect(decorated.groups.map((g) => g.rows.map((r) => [r.target.id, r.num]))).toEqual(
      plain.groups.map((g) => g.rows.map((r) => [r.target.id, r.num])),
    );
    // Favorites borrow their twin's number, so 'Open all' and 1–9 shortcuts
    // are untouched by starring.
    expect(decorated.favorites.map((f) => [f.id, f.num])).toEqual([
      ["t-2", 2],
      ["t-3", 3],
    ]);
  });
});
