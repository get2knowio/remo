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
