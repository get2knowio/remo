// WorkspacePane's wiring: does the computed layout actually reach the DOM, and
// — the one that matters — can a layout change ever remount a TerminalCard?
//
// A remount re-runs the card's [target.id] effect, whose cleanup calls
// connection.close() and adapter.dispose(): the SSH connection drops and the
// scrollback goes with it. Master/stack changes which slot a tile occupies on
// every mastership change, so this is the feature's central structural risk.
// The guarantee is "one flat keyed child list; slots are grid-area styles on
// each card's own root, never wrapper elements", and the test below is what
// proves it rather than merely documenting it.

import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionTarget } from "../api/client";

const adapters: { dispose: ReturnType<typeof vi.fn> }[] = [];

vi.mock("../terminal/defaultRenderer", () => ({
  createDefaultRenderer: vi.fn(() => {
    const adapter = {
      open: vi.fn(),
      write: vi.fn(),
      onData: vi.fn().mockReturnValue(() => {}),
      fit: vi.fn().mockReturnValue({ cols: 80, rows: 24 }),
      resize: vi.fn(),
      applyFont: vi.fn(),
      applyTheme: vi.fn(),
      focus: vi.fn(),
      onTitleChange: vi.fn().mockReturnValue(() => {}),
      onSelectionChange: vi.fn().mockReturnValue(() => {}),
      getSelection: vi.fn().mockReturnValue(null),
      copySelection: vi.fn().mockResolvedValue(false),
      dispose: vi.fn(),
    };
    adapters.push(adapter);
    return adapter;
  }),
}));

vi.mock("../terminal/TerminalConnection", () => ({
  TerminalConnection: class {
    needsManualReconnect = false;
    connect = vi.fn().mockResolvedValue(undefined);
    close = vi.fn().mockResolvedValue(undefined);
    sendInput = vi.fn();
    sendResize = vi.fn();
  },
}));

// jsdom has neither; useDroppable and TerminalCard both observe their nodes.
class NoopResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;

const target = (id: string): SessionTarget =>
  ({ id, project: id, instance_type: "incus", instance_name: "box" }) as unknown as SessionTarget;

async function mount(ids: string[]) {
  vi.resetModules();
  window.localStorage.clear();
  adapters.length = 0;
  const workspaceMod = await import("../state/workspace");
  const { WorkspacePane } = await import("./WorkspacePane");
  const targetsById = new Map(ids.map((id) => [id, target(id)]));

  const view = render(
    <WorkspacePane
      targetsById={targetsById}
      regionByKey={new Map()}
      onTerminalEnded={() => {}}
      onTerminalStarted={() => {}}
      narrow={false}
    />,
  );
  // Drive the store directly: the rail isn't mounted here.
  const store = renderStore(workspaceMod);
  act(() => store.openMany(ids.map(target)));
  return { view, store };
}

/** The store's actions, outside React. `useWorkspace` returns them, but this
 * file never renders a consumer of its own. */
function renderStore(mod: typeof import("../state/workspace")) {
  let api!: ReturnType<typeof mod.useWorkspace>;
  function Probe(): null {
    api = mod.useWorkspace();
    return null;
  }
  render(<Probe />);
  return new Proxy({} as ReturnType<typeof mod.useWorkspace>, {
    get: (_t, key) => api[key as keyof typeof api],
  });
}

const body = (): HTMLElement => screen.getByTestId("workspace").querySelector(".workspace-body")!;

beforeEach(() => {
  window.localStorage.clear();
});

describe("WorkspacePane layout wiring", () => {
  it("renders the uniform grid exactly as before when nothing is tiled", async () => {
    await mount(["a", "b", "c"]);
    expect(body().style.gridTemplateColumns).toBe("repeat(2, minmax(0, 1fr))");
    expect(body().style.gridTemplateRows).toBe("repeat(2, minmax(0, 1fr))");
    // No explicit placement: tiles auto-place, as they always have.
    expect(screen.getByTestId("terminal-card-a").style.gridArea).toBe("");
  });

  it("pushes the master template and every tile's slot into the DOM", async () => {
    const { store } = await mount(["a", "b", "c"]);
    act(() => store.setMaster("c", "right"));

    expect(body().style.gridTemplateColumns).toBe("repeat(1, minmax(0, 40fr)) minmax(0, 60fr)");
    expect(screen.getByTestId("terminal-card-c").style.gridArea).toBe("1 / 2 / 3 / 3");
    expect(screen.getByTestId("terminal-card-a").style.gridArea).toBe("1 / 1 / 2 / 2");
    expect(screen.getByTestId("terminal-card-b").style.gridArea).toBe("2 / 1 / 3 / 2");
  });

  it("offers the four edge zones only while a reorder is possible", async () => {
    const { store } = await mount(["a", "b", "c"]);
    expect(screen.getAllByTestId(/^snap-zone-/)).toHaveLength(4);

    // Single view: nothing to reorder, nothing to snap onto.
    act(() => store.selectOnly(target("a")));
    expect(screen.queryAllByTestId(/^snap-zone-/)).toHaveLength(0);
  });

  it("hides the edge zones while a terminal is fullscreen", async () => {
    const { store } = await mount(["a", "b"]);
    act(() => store.maximize("a"));
    expect(screen.queryAllByTestId(/^snap-zone-/)).toHaveLength(0);
  });

  // THE structural guarantee. Mutation: wrap the master (or the stack) in a
  // slot <div> in WorkspacePane and this must fail — a card changing parent
  // remounts, dropping its SSH connection and scrollback.
  it("never remounts a terminal across layout changes", async () => {
    const { store } = await mount(["a", "b", "c"]);
    const surfaceBefore = screen.getByTestId("terminal-surface-a");
    const buildsBefore = adapters.length;

    act(() => store.setMaster("a", "left"));
    act(() => store.setMaster("a", "top"));
    act(() => store.setMaster("b", "right"));
    act(() => store.clearMaster());

    expect(Object.is(screen.getByTestId("terminal-surface-a"), surfaceBefore)).toBe(true);
    expect(adapters).toHaveLength(buildsBefore);
    for (const adapter of adapters) {
      expect(adapter.dispose).not.toHaveBeenCalled();
    }
  });

  // The ⊞ control is dead UI in a plain grid; when the grid is TILED it becomes
  // the way out, so "how do I undo this" has an obvious answer that isn't
  // "cycle the ▦ button forward until it wraps".
  it("flattens a tiling from the grid control, and stays inert in a plain grid", async () => {
    const { store } = await mount(["a", "b", "c"]);
    const gridBtn = (): HTMLButtonElement =>
      screen.getByTestId("terminal-grid-a") as HTMLButtonElement;

    expect(gridBtn().disabled).toBe(true);

    act(() => store.setMaster("c", "right"));
    expect(gridBtn().disabled).toBe(false);
    expect(gridBtn().getAttribute("title")).toBe("Even out the grid");

    act(() => gridBtn().click());
    expect(store.layout).toEqual({ kind: "grid" });
    expect(gridBtn().disabled).toBe(true);
  });

  it("cycles a tile through the sides from its header control", async () => {
    const { store } = await mount(["a", "b"]);
    const button = (): HTMLElement => screen.getByTestId("terminal-tile-a");

    act(() => button().click());
    expect(store.layout).toMatchObject({ kind: "master", id: "a", side: "left" });
    expect(button().getAttribute("aria-pressed")).toBe("true");

    act(() => button().click());
    expect(store.layout).toMatchObject({ side: "top" });

    act(() => button().click());
    act(() => button().click());
    act(() => button().click());
    expect(store.layout).toEqual({ kind: "grid" });
    expect(button().getAttribute("aria-pressed")).toBe("false");
  });
});

describe("tiling control outside the grid", () => {
  // It sits in the same cluster as ⊞ / ◻ / ⤢, which are always present and
  // merely disabled when inapplicable. Vanishing was the odd one out.
  it("stays in the cluster in a single view, and rebuilds the grid as master", async () => {
    const { store } = await mount(["a", "b", "c"]);
    act(() => store.soloTile("b"));

    const button = screen.getByTestId("terminal-tile-b") as HTMLButtonElement;
    expect(button.disabled).toBe(false);

    act(() => button.click());
    // Back to the remembered grid, with the tile you were looking at mastering.
    expect(store.visible).toEqual(["a", "b", "c"]);
    expect(store.layout).toMatchObject({ kind: "master", id: "b", side: "left" });
  });

  it("takes mastership from a remembered tiling rather than restoring it", async () => {
    const { store } = await mount(["a", "b", "c"]);
    act(() => store.setMaster("c", "right"));
    act(() => store.soloTile("a"));

    act(() => (screen.getByTestId("terminal-tile-a") as HTMLButtonElement).click());
    expect(store.layout).toMatchObject({ kind: "master", id: "a" });
  });

  it("is present but inert when there is no grid to build", async () => {
    await mount(["a"]);
    const button = screen.getByTestId("terminal-tile-a") as HTMLButtonElement;
    expect(button).toBeTruthy();
    expect(button.disabled).toBe(true);
  });
});

describe("tiling split setting", () => {
  // The point of putting the split in settings rather than the layout: changing
  // it re-flows the tiling you are looking at, no re-tiling required.
  it("re-flows the current tiling when the split changes", async () => {
    const { store } = await mount(["a", "b", "c"]);
    const settings = await import("../state/settings");
    act(() => store.setMaster("c", "right"));

    const cols = (): string => body().style.gridTemplateColumns;
    expect(cols()).toBe("repeat(1, minmax(0, 40fr)) minmax(0, 60fr)");

    act(() => settings.settingsActions.setMasterSplit(0.5));
    expect(cols()).toBe("repeat(1, minmax(0, 50fr)) minmax(0, 50fr)");

    act(() => settings.settingsActions.setMasterSplit(0.55));
    expect(cols()).toBe("repeat(1, minmax(0, 45fr)) minmax(0, 55fr)");
  });
});

describe("terminal chrome toggle", () => {
  it("drops every tile header when chrome is off, and restores them", async () => {
    const { view } = await mount(["a", "b"]);
    const settings = await import("../state/settings");
    const headers = (): number => view.container.querySelectorAll(".terminal-card-header").length;

    expect(headers()).toBe(2);
    act(() => settings.settingsActions.toggleTileChrome());
    expect(headers()).toBe(0);
    act(() => settings.settingsActions.toggleTileChrome());
    expect(headers()).toBe(2);
  });

  // Fullscreen is the one place the header must stay: there is one card on
  // screen so the saved space is negligible, and its controls are the way out.
  it("keeps the header on a fullscreen terminal", async () => {
    const { view, store } = await mount(["a", "b"]);
    const settings = await import("../state/settings");
    act(() => settings.settingsActions.toggleTileChrome());
    act(() => store.maximize("a"));

    expect(view.container.querySelectorAll(".terminal-card-header")).toHaveLength(1);
  });
});
