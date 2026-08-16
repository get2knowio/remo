// The diagnostics snapshot: ordering, robustness, and the redaction contract.
//
// Two properties matter more than the field values. (1) `collectDiagnostics()`
// is the escape hatch used when the console is broken, so it must be total — a
// pane whose provider throws degrades to a stub, never taking the snapshot down
// with it. (2) The blob is meant to be pasted into a public bug report, so what
// it may contain is an allowlist, checked here rather than assumed.

import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getHealth: vi.fn(async () => ({ status: "alive", version: "9.9.9" })),
}));

vi.mock("../api/client", () => ({ getHealth: mocks.getHealth }));

const WORKSPACE_KEY = "remo-web:workspace";

/** Fresh module graph + a seeded workspace, since both stores are singletons. */
async function load(persisted?: {
  attached: string[];
  visible: string[];
  focusedId?: string | null;
}): Promise<typeof import("./diagnostics")> {
  vi.resetModules();
  window.localStorage.clear();
  if (persisted) {
    window.localStorage.setItem(
      WORKSPACE_KEY,
      JSON.stringify({
        attached: persisted.attached,
        visible: persisted.visible,
        focusedId: persisted.focusedId ?? persisted.visible[0] ?? null,
        layout: { kind: "grid" },
      }),
    );
  }
  return import("./diagnostics");
}

/** A complete, realistic pane entry — the shape a real TerminalCard supplies. */
function pane(id: string): import("./diagnostics").PaneDiagnostics {
  return {
    id,
    target: { project: "demo", instanceType: "incus", instanceName: "box" },
    visible: true,
    focused: false,
    connection: {
      state: "ready",
      needsManualReconnect: false,
      reconnectAttempts: 0,
      lastSentGrid: { cols: 100, rows: 40 },
      lastClose: null,
      droppedControlFrames: 0,
      socket: { readyState: 1, bufferedAmount: 0 },
    },
    geometry: {
      containerPx: { top: 48, left: 0, width: 800, height: 600, bottom: 648 },
      fitLoop: { lastSent: { cols: 100, rows: 40 }, pending: false, lastSkipReason: null },
    },
    renderer: {
      kind: "webgl",
      addons: ["fit", "webgl"],
      grid: { cols: 100, rows: 40 },
      proposedGrid: { cols: 100, rows: 40 },
      containerPx: { top: 48, left: 0, width: 800, height: 600, bottom: 648 },
      modes: {
        applicationCursorKeysMode: false,
        applicationKeypadMode: false,
        bracketedPasteMode: true,
        insertMode: false,
        mouseTrackingMode: "any",
        originMode: false,
        reverseWraparoundMode: false,
        sendFocusMode: false,
        wraparoundMode: true,
      },
    },
    font: { family: "Menlo", size: 13, ligatures: false },
    themeLabel: "Remo Dark",
    rttMs: 12,
  };
}

beforeEach(() => {
  mocks.getHealth.mockClear();
  mocks.getHealth.mockResolvedValue({ status: "alive", version: "9.9.9" });
});

describe("collectDiagnostics", () => {
  it("orders panes visible-first, then attached-but-hidden", async () => {
    // Mirrors WorkspacePane's render order, so a pasted snapshot reads in the
    // same order as the screen it describes.
    const diag = await load({ attached: ["a", "b", "c"], visible: ["c", "a"] });
    for (const id of ["a", "b", "c"]) {
      diag.registerPaneDiagnostics(id, () => pane(id));
    }

    expect(diag.collectDiagnostics().panes.map((p) => p.id)).toEqual(["c", "a", "b"]);
  });

  it("includes a registered pane the workspace no longer lists", async () => {
    const diag = await load({ attached: [], visible: [] });
    diag.registerPaneDiagnostics("orphan", () => pane("orphan"));

    expect(diag.collectDiagnostics().panes.map((p) => p.id)).toEqual(["orphan"]);
  });

  it("drops a pane once it unregisters", async () => {
    const diag = await load({ attached: ["a"], visible: ["a"] });
    diag.registerPaneDiagnostics("a", () => pane("a"));
    diag.removePaneDiagnostics("a");

    expect(diag.collectDiagnostics().panes).toEqual([]);
  });

  it("degrades a throwing provider to a stub instead of failing the snapshot", async () => {
    const diag = await load({ attached: ["a", "b"], visible: ["a", "b"] });
    diag.registerPaneDiagnostics("a", () => {
      throw new Error("adapter is gone");
    });
    diag.registerPaneDiagnostics("b", () => pane("b"));

    const snapshot = diag.collectDiagnostics();

    expect(snapshot.panes[0]).toEqual({ id: "a", error: "adapter is gone" });
    // The healthy pane still reports — one broken card must not cost the rest.
    expect(snapshot.panes[1]).toMatchObject({ id: "b", renderer: { kind: "webgl" } });
  });

  it("reports the layout axes the console actually renders", async () => {
    const diag = await load({ attached: ["a", "b"], visible: ["a", "b"] });

    const { layout, env } = diag.collectDiagnostics();

    expect(layout).toMatchObject({
      kind: "grid",
      masterId: null,
      // Two visible tiles: the single-vs-grid axis says grid.
      paneMode: "grid",
      attached: 2,
      visible: 2,
    });
    expect(env.viewport.width).toBe(window.innerWidth);
    // jsdom has no visualViewport; a missing API must not throw.
    expect(env.visualViewportScale).toBeNull();
  });
});

describe("service version", () => {
  it("is null until /health answers, then cached", async () => {
    const diag = await load();

    expect(diag.collectDiagnostics().versions.service).toBeNull();

    await diag.ensureServiceVersion();
    expect(diag.collectDiagnostics().versions.service).toBe("9.9.9");

    // Cached: the version cannot change without a reload.
    await diag.ensureServiceVersion();
    expect(mocks.getHealth).toHaveBeenCalledTimes(1);
  });

  it("survives an unreachable service and retries next time", async () => {
    const diag = await load();
    mocks.getHealth.mockRejectedValueOnce(new Error("network"));

    await expect(diag.ensureServiceVersion()).resolves.toBeNull();
    // A failure is NOT cached, or a snapshot taken during an outage would
    // permanently claim an unknown version.
    await expect(diag.ensureServiceVersion()).resolves.toBe("9.9.9");
  });
});

// ---------------------------------------------------------------------------
// Redaction contract.
//
// The blob is meant to be pasted into a public bug report. The exclusions are
// enforced at the source (TerminalConnection reports readyState/bufferedAmount
// and nothing else, because the auth token rides as a WS subprotocol value),
// but the allowlist below is what makes ADDING a field a deliberate act: a new
// key on a pane fails this test until it is listed.
// ---------------------------------------------------------------------------
describe("redaction contract", () => {
  const PANE_KEYS = [
    "connection",
    "focused",
    "font",
    "geometry",
    "id",
    "renderer",
    "rttMs",
    "target",
    "themeLabel",
    "visible",
  ];
  const CONNECTION_KEYS = [
    "droppedControlFrames",
    "lastClose",
    "lastSentGrid",
    "needsManualReconnect",
    "reconnectAttempts",
    "socket",
    "state",
  ];

  it("carries exactly the fields the contract allows", async () => {
    const diag = await load({ attached: ["a"], visible: ["a"] });
    diag.registerPaneDiagnostics("a", () => pane("a"));

    const [entry] = diag.collectDiagnostics().panes;

    expect(Object.keys(entry).sort()).toEqual(PANE_KEYS);
    expect(Object.keys((entry as import("./diagnostics").PaneDiagnostics).connection).sort()).toEqual(
      CONNECTION_KEYS,
    );
  });

  it("never serializes a token, a socket URL, or terminal contents", async () => {
    // A sentinel sweep over a full, realistic snapshot. The exclusions
    // themselves are enforced (and pinned) where the data originates —
    // TerminalConnection.test.ts's "never its url or protocol" — so this is the
    // end-to-end backstop: whatever the pipeline assembles, none of it lands.
    const diag = await load({ attached: ["a"], visible: ["a"] });
    diag.registerPaneDiagnostics("a", () => pane("a"));

    const serialized = JSON.stringify(diag.collectDiagnostics());

    expect(serialized).not.toContain("ws_token");
    expect(serialized).not.toContain("ws://");
    expect(serialized).not.toContain("remo-terminal.v1");
  });
});
