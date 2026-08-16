// Terminal-theme wiring for a card: the renderer is SEEDED with the effective
// theme, later changes are applied to the LIVE terminal (never by remounting
// it, which would drop the connection and scrollback), and a per-target
// override takes precedence over the Settings-wide default.

import { act, render, screen } from "@testing-library/react";
import { DndContext } from "@dnd-kit/core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionTarget } from "../api/client";
import { themeMenuPosition } from "./TerminalCard";
import type { RendererAdapter } from "../terminal/RendererAdapter";

const adapters: RendererAdapter[] = [];

function fakeAdapter(): RendererAdapter {
  return {
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
    diagnostics: vi.fn().mockReturnValue({
      kind: "dom",
      addons: ["fit"],
      grid: { cols: 80, rows: 24 },
      proposedGrid: { cols: 80, rows: 24 },
      containerPx: { top: 0, left: 0, width: 640, height: 400, bottom: 400 },
      modes: {
        applicationCursorKeysMode: false,
        applicationKeypadMode: false,
        bracketedPasteMode: false,
        insertMode: false,
        mouseTrackingMode: "none",
        originMode: false,
        reverseWraparoundMode: false,
        sendFocusMode: false,
        wraparoundMode: true,
      },
    }),
    dispose: vi.fn(),
  };
}

vi.mock("../terminal/defaultRenderer", () => ({
  createDefaultRenderer: vi.fn((_font?: unknown, theme?: unknown) => {
    const adapter = fakeAdapter();
    // Remember what the renderer was constructed with — the seeding contract.
    (adapter as unknown as { seededTheme: unknown }).seededTheme = theme;
    adapters.push(adapter);
    return adapter;
  }),
}));

const connections: Array<{ reassertSize: ReturnType<typeof vi.fn> }> = [];

vi.mock("../terminal/TerminalConnection", () => ({
  TerminalConnection: class {
    needsManualReconnect = false;
    connect = vi.fn().mockResolvedValue(undefined);
    close = vi.fn().mockResolvedValue(undefined);
    sendInput = vi.fn();
    sendResize = vi.fn();
    reassertSize = vi.fn();
    diagnostics = vi.fn().mockReturnValue({
      state: "ready",
      needsManualReconnect: false,
      reconnectAttempts: 0,
      lastSentGrid: { cols: 80, rows: 24 },
      lastClose: null,
      droppedControlFrames: 0,
      socket: { readyState: 1, bufferedAmount: 0 },
    });
    constructor() {
      connections.push(this as unknown as { reassertSize: ReturnType<typeof vi.fn> });
    }
  },
}));

// jsdom has neither; TerminalCard observes its container and coalesces fits.
class NoopResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
globalThis.ResizeObserver = NoopResizeObserver as unknown as typeof ResizeObserver;

const target = {
  id: "t1",
  project: "demo",
  instance_type: "incus",
  instance_name: "box",
} as unknown as SessionTarget;

async function mount(): Promise<{
  settings: typeof import("../state/settings");
  card: typeof import("./TerminalCard");
  diagnostics: typeof import("../state/diagnostics");
}> {
  vi.resetModules();
  adapters.length = 0;
  connections.length = 0;
  window.localStorage.clear();
  const settings = await import("../state/settings");
  const card = await import("./TerminalCard");
  const diagnostics = await import("../state/diagnostics");
  return { settings, card, diagnostics };
}

function renderCard(TerminalCard: typeof import("./TerminalCard").TerminalCard): void {
  render(
    <DndContext>
      <TerminalCard
        target={target}
        mode="single"
        isVisible
        isFocused
        viewState="normal"
        onClose={() => {}}
        onNormal={() => {}}
        onToggleFullscreen={() => {}}
      />
    </DndContext>,
  );
}

const seeded = (adapter: RendererAdapter): { background: string } =>
  (adapter as unknown as { seededTheme: { background: string } }).seededTheme;

beforeEach(() => {
  window.localStorage.clear();
});

describe("TerminalCard terminal theme", () => {
  it("seeds the renderer with the effective theme so the first frame is correct", async () => {
    const { settings, card } = await mount();
    settings.settingsActions.setTermTheme("dracula");
    renderCard(card.TerminalCard);

    expect(adapters).toHaveLength(1);
    expect(seeded(adapters[0]).background).toBe("#282a36");
  });

  it("seeds the site-matched theme by default (no explicit choice)", async () => {
    const { card } = await mount();
    renderCard(card.TerminalCard);
    // jsdom reports no OS preference, so the store's dark default stands.
    expect(seeded(adapters[0]).background).toBe("#040608");
  });

  it("follows the site mode while the global choice is 'auto'", async () => {
    const { settings, card } = await mount();
    renderCard(card.TerminalCard);

    act(() => settings.settingsActions.setThemeMode("light"));
    expect(adapters[0].applyTheme).toHaveBeenLastCalledWith(
      expect.objectContaining({ background: "#eff2f6" }),
    );
    expect(adapters).toHaveLength(1);
  });

  it("recolors the live terminal without rebuilding it", async () => {
    const { settings, card } = await mount();
    renderCard(card.TerminalCard);
    expect(adapters).toHaveLength(1);

    act(() => settings.settingsActions.setTermTheme("gruvbox-light"));

    expect(adapters[0].applyTheme).toHaveBeenCalledWith(
      expect.objectContaining({ background: "#fbf1c7", foreground: "#3c3836" }),
    );
    // The whole point: no second renderer, so the connection and scrollback of
    // the first one survive the theme switch.
    expect(adapters).toHaveLength(1);
    expect(adapters[0].dispose).not.toHaveBeenCalled();
  });

  it("lets a per-target override win, and clearing it restores the global default", async () => {
    const { settings, card } = await mount();
    settings.settingsActions.setTermTheme("gruvbox-dark");
    renderCard(card.TerminalCard);
    expect(seeded(adapters[0]).background).toBe("#282828");

    act(() => settings.settingsActions.setTermThemeOverride(target.id, "catppuccin-latte"));
    expect(adapters[0].applyTheme).toHaveBeenLastCalledWith(
      expect.objectContaining({ background: "#eff1f5" }),
    );

    act(() => settings.settingsActions.setTermThemeOverride(target.id, null));
    expect(adapters[0].applyTheme).toHaveBeenLastCalledWith(
      expect.objectContaining({ background: "#282828" }),
    );
  });

  it("pins --bg-term to the card's theme so the surface gutter matches", async () => {
    const { settings, card } = await mount();
    settings.settingsActions.setTermTheme("solarized-light");
    renderCard(card.TerminalCard);

    const tile = screen.getByTestId(`terminal-card-${target.id}`);
    expect(tile.style.getPropertyValue("--bg-term")).toBe("#fdf6e3");
  });

  it("offers the theme picker, selecting an override and clearing it back to default", async () => {
    const { settings, card } = await mount();
    renderCard(card.TerminalCard);

    act(() => screen.getByTestId(`terminal-theme-${target.id}`).click());
    act(() => screen.getByRole("menuitem", { name: /Dracula/ }).click());
    expect(settings.getSettings().termThemeOverrides).toEqual({ [target.id]: "dracula" });

    act(() => screen.getByTestId(`terminal-theme-${target.id}`).click());
    act(() => screen.getByTestId(`terminal-theme-default-${target.id}`).click());
    expect(settings.getSettings().termThemeOverrides).toEqual({});
  });

  it("closes the theme picker on Escape", async () => {
    const { card } = await mount();
    renderCard(card.TerminalCard);

    act(() => screen.getByTestId(`terminal-theme-${target.id}`).click());
    expect(screen.getByRole("menu")).toBeInTheDocument();

    act(() => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

// The reported bug: in a 2x2 grid on a small display the theme list was cut off
// at the card's edge (`.terminal-card` sets `overflow: hidden`) and swiping it
// scrolled the whole page instead. The menu is now placed against the viewport
// with a computed max-height, which is what gives it something to scroll.
describe("themeMenuPosition", () => {
  const VIEWPORT = { width: 1024, height: 768 };

  it("opens below the swatch when there is room, right-aligned to it", () => {
    const pos = themeMenuPosition({ top: 100, bottom: 126, right: 500 }, VIEWPORT);
    expect(pos.placement).toBe("below");
    expect(pos.style).toMatchObject({ position: "fixed", top: 132, left: 500 - 216 });
  });

  it("caps the height to the space available, so the list scrolls instead of overflowing", () => {
    // Room below is 768 - 560 - 6 - 8 = 194: enough to stay below, but well
    // short of the ~300px the nine-entry list wants. That gap is the scroll.
    const pos = themeMenuPosition({ top: 534, bottom: 560, right: 500 }, VIEWPORT);
    expect(pos.placement).toBe("below");
    expect(pos.style.maxHeight).toBe(194);
  });

  it("floors the height so a cramped anchor still shows a usable list", () => {
    // Below is only 154 here, and above (560) is roomier — so it flips, rather
    // than rendering a sliver.
    const pos = themeMenuPosition({ top: 574, bottom: 600, right: 500 }, VIEWPORT);
    expect(pos.placement).toBe("above");
    expect(pos.style.maxHeight).toBe(560);
  });

  it("flips above the swatch when below is too cramped and above is roomier", () => {
    const pos = themeMenuPosition({ top: 700, bottom: 726, right: 500 }, VIEWPORT);
    expect(pos.placement).toBe("above");
    // Anchored by its bottom edge, so it grows upward without needing its height.
    expect(pos.style.bottom).toBe(768 - 700 + 6);
    expect(pos.style.top).toBeUndefined();
  });

  it("never places the menu off the right edge", () => {
    const pos = themeMenuPosition({ top: 40, bottom: 66, right: 1020 }, VIEWPORT);
    expect(pos.style.left).toBe(1024 - 216 - 8);
  });

  it("never places the menu off the left edge on a narrow screen", () => {
    const pos = themeMenuPosition({ top: 40, bottom: 66, right: 120 }, { width: 380, height: 800 });
    expect(pos.style.left).toBe(8);
  });

  // A card mid-teardown (or jsdom, which reports zeroes) must not produce a
  // negative max-height and an unusable sliver of a menu.
  it("stays usable for a degenerate anchor", () => {
    const pos = themeMenuPosition({ top: 0, bottom: 0, right: 0 }, { width: 0, height: 0 });
    expect(pos.style.maxHeight as number).toBeGreaterThan(0);
    expect(pos.style.left as number).toBeGreaterThanOrEqual(0);
  });
});


// ---------------------------------------------------------------------------
// Remote size repair on show.
//
// A hidden card measures 0x0, so scheduleFit skips every fit while it is away.
// On return, a fit landing on the SAME cols/rows sends nothing — correct for
// the local emulator, but it leaves no way to repair a remote that missed the
// SIGWINCH for this size, and nothing else will ever re-send it. That is what
// stranded `stty size` at 67 rows against a panel with room for 59.
// ---------------------------------------------------------------------------
describe("TerminalCard remote size repair", () => {
  function renderWithVisibility(
    TerminalCard: typeof import("./TerminalCard").TerminalCard,
    isVisible: boolean,
  ) {
    return render(
      <DndContext>
        <TerminalCard
          target={target}
          mode="single"
          isVisible={isVisible}
          isFocused
          viewState="normal"
          onClose={() => {}}
          onNormal={() => {}}
          onToggleFullscreen={() => {}}
        />
      </DndContext>,
    );
  }

  it("re-asserts the remote size when a hidden card is shown again", async () => {
    const { card } = await mount();
    const { rerender } = renderWithVisibility(card.TerminalCard, false);

    expect(connections).toHaveLength(1);
    const connection = connections[0];
    // Hidden on mount: nothing to repair, and a hidden card must not touch the
    // remote at all.
    expect(connection.reassertSize).not.toHaveBeenCalled();

    rerender(
      <DndContext>
        <card.TerminalCard
          target={target}
          mode="single"
          isVisible
          isFocused
          viewState="normal"
          onClose={() => {}}
          onNormal={() => {}}
          onToggleFullscreen={() => {}}
        />
      </DndContext>,
    );

    expect(connection.reassertSize).toHaveBeenCalledTimes(1);
  });

  it("re-asserts on mount when the card starts visible", async () => {
    const { card } = await mount();
    renderWithVisibility(card.TerminalCard, true);

    expect(connections).toHaveLength(1);
    // Harmless before the socket is open — sendControl drops it, and the
    // `ready` handler re-sends the same dims — but it is the right shape: the
    // card asserts its size whenever it is on screen.
    expect(connections[0].reassertSize).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Diagnostics registration.
//
// The card is the only holder of its adapter/connection/fit-loop, so the
// diagnostics snapshot can only see a pane that registered itself here — and
// must not keep seeing one after it unmounts (a stale provider would call into
// a disposed adapter).
// ---------------------------------------------------------------------------
describe("TerminalCard diagnostics", () => {
  it("registers this pane while mounted and removes it on unmount", async () => {
    const { card, diagnostics } = await mount();
    const { unmount } = render(
      <DndContext>
        <card.TerminalCard
          target={target}
          mode="single"
          isVisible
          isFocused
          viewState="normal"
          onClose={() => {}}
          onNormal={() => {}}
          onToggleFullscreen={() => {}}
        />
      </DndContext>,
    );

    const entry = diagnostics
      .collectDiagnostics()
      .panes.find((p) => p.id === target.id);
    expect(entry).toBeDefined();
    expect(entry).toMatchObject({
      visible: true,
      focused: true,
      target: { project: "demo", instanceType: "incus", instanceName: "box" },
      connection: { state: "ready" },
      renderer: { kind: "dom" },
    });

    unmount();
    expect(diagnostics.collectDiagnostics().panes).toHaveLength(0);
  });

  it("reports a hidden pane as attached-but-invisible", async () => {
    // The tab-switch resize evidence: a card kept mounted behind display:none
    // still answers, so a snapshot shows what the pane was doing while away.
    const { card, diagnostics } = await mount();
    render(
      <DndContext>
        <card.TerminalCard
          target={target}
          mode="single"
          isVisible={false}
          isFocused={false}
          viewState="normal"
          onClose={() => {}}
          onNormal={() => {}}
          onToggleFullscreen={() => {}}
        />
      </DndContext>,
    );

    expect(diagnostics.collectDiagnostics().panes[0]).toMatchObject({
      id: target.id,
      visible: false,
      focused: false,
    });
  });
});
