// Terminal-theme wiring for a card: the renderer is SEEDED with the effective
// theme, later changes are applied to the LIVE terminal (never by remounting
// it, which would drop the connection and scrollback), and a per-target
// override takes precedence over the Settings-wide default.

import { act, render, screen } from "@testing-library/react";
import { DndContext } from "@dnd-kit/core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SessionTarget } from "../api/client";
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

vi.mock("../terminal/TerminalConnection", () => ({
  TerminalConnection: class {
    needsManualReconnect = false;
    connect = vi.fn().mockResolvedValue(undefined);
    close = vi.fn().mockResolvedValue(undefined);
    sendInput = vi.fn();
    sendResize = vi.fn();
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
}> {
  vi.resetModules();
  adapters.length = 0;
  window.localStorage.clear();
  const settings = await import("../state/settings");
  const card = await import("./TerminalCard");
  return { settings, card };
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
