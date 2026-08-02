import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const STORAGE_KEY = "remo-web:settings";

// jsdom has no matchMedia, and the store treats it as optional (initSettings
// bails out without it). Stub it so the "system" path is actually exercised,
// and hand back an `emit` so a test can simulate the OS flipping theme.
function stubMatchMedia(dark: boolean): { emit: (next: boolean) => void } {
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  const mql = {
    matches: dark,
    media: "(prefers-color-scheme: dark)",
    onchange: null,
    addEventListener: (_type: string, listener: (e: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: (e: MediaQueryListEvent) => void) => {
      listeners.delete(listener);
    },
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  };
  window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;
  return {
    emit(next: boolean) {
      mql.matches = next;
      for (const listener of listeners) {
        listener({ matches: next } as MediaQueryListEvent);
      }
    },
  };
}

// The store is a module singleton persisted to localStorage; reset the module
// registry, storage, and the <html> attribute it writes so each test starts
// from a genuinely cold app (workspace.test.ts pattern).
async function load(): Promise<typeof import("./settings")> {
  vi.resetModules();
  return import("./settings");
}

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  stubMatchMedia(true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("site theme mode", () => {
  it("defaults to system and writes no data-theme attribute", async () => {
    const settings = await load();
    settings.initSettings();

    expect(settings.getSettings().themeMode).toBe("system");
    // No attribute at all: tokens.css's `color-scheme: light dark` resolves the
    // OS preference itself, with no JS in the rendering path.
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("pins the attribute for an explicit choice and clears it back on system", async () => {
    const settings = await load();
    settings.initSettings();

    act(() => settings.settingsActions.setThemeMode("dark"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    act(() => settings.settingsActions.setThemeMode("light"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    act(() => settings.settingsActions.setThemeMode("system"));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("round-trips the choice through localStorage", async () => {
    const first = await load();
    first.initSettings();
    act(() => first.settingsActions.setThemeMode("light"));

    document.documentElement.removeAttribute("data-theme");
    const reloaded = await load();
    reloaded.initSettings();

    expect(reloaded.getSettings().themeMode).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("cycles system → light → dark → system", async () => {
    const settings = await load();
    settings.initSettings();

    const seen: string[] = [];
    for (let i = 0; i < 3; i += 1) {
      act(() => settings.settingsActions.cycleThemeMode());
      seen.push(settings.getSettings().themeMode);
    }
    expect(seen).toEqual(["light", "dark", "system"]);
  });

  it("resolves 'system' from the OS preference, live", async () => {
    const media = stubMatchMedia(false);
    const settings = await load();
    settings.initSettings();

    expect(settings.getSettings().systemPrefersDark).toBe(false);
    expect(settings.resolvedSiteTheme()).toBe("light");

    act(() => media.emit(true));
    expect(settings.getSettings().systemPrefersDark).toBe(true);
    expect(settings.resolvedSiteTheme()).toBe("dark");
  });

  it("ignores the OS preference once the user has chosen a mode", async () => {
    const media = stubMatchMedia(true);
    const settings = await load();
    settings.initSettings();
    act(() => settings.settingsActions.setThemeMode("light"));

    act(() => media.emit(false));
    expect(settings.resolvedSiteTheme()).toBe("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("never persists the derived OS preference", async () => {
    const settings = await load();
    settings.initSettings();
    act(() => settings.settingsActions.setThemeMode("dark"));

    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}") as Record<
      string,
      unknown
    >;
    expect(stored.themeMode).toBe("dark");
    expect(stored).not.toHaveProperty("systemPrefersDark");
  });

  it("falls back to system when the persisted mode is garbage", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ themeMode: "neon" }));
    const settings = await load();
    settings.initSettings();

    expect(settings.getSettings().themeMode).toBe("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});

describe("terminal themes", () => {
  it("defaults to following the site theme, with no overrides", async () => {
    const settings = await load();
    expect(settings.getSettings().termTheme).toBe("auto");
    expect(settings.getSettings().termThemeOverrides).toEqual({});
  });

  // The point of the default: a light console never wraps a dark terminal.
  it("resolves the default against the site mode, live", async () => {
    const media = stubMatchMedia(true);
    const settings = await load();
    settings.initSettings();
    expect(settings.effectiveTerminalTheme(settings.getSettings(), "a").id).toBe("remo-dark");

    // Explicit light: the terminal follows immediately.
    act(() => settings.settingsActions.setThemeMode("light"));
    expect(settings.effectiveTerminalTheme(settings.getSettings(), "a").id).toBe("remo-light");

    // Back to "system", and the OS itself flipping is enough to switch it.
    act(() => settings.settingsActions.setThemeMode("system"));
    expect(settings.effectiveTerminalTheme(settings.getSettings(), "a").id).toBe("remo-dark");
    act(() => media.emit(false));
    expect(settings.effectiveTerminalTheme(settings.getSettings(), "a").id).toBe("remo-light");
  });

  it("stops following the site once a concrete theme is chosen", async () => {
    const settings = await load();
    settings.initSettings();
    act(() => settings.settingsActions.setTermTheme("dracula"));

    act(() => settings.settingsActions.setThemeMode("light"));
    expect(settings.effectiveTerminalTheme(settings.getSettings(), "a").id).toBe("dracula");
  });

  it("labels a selection, naming what 'auto' currently resolves to", async () => {
    const settings = await load();
    settings.initSettings();
    act(() => settings.settingsActions.setThemeMode("dark"));
    expect(settings.terminalThemeLabel("auto")).toBe("Follow site theme — Remo Dark");
    act(() => settings.settingsActions.setThemeMode("light"));
    expect(settings.terminalThemeLabel("auto")).toBe("Follow site theme — Remo Light");
    expect(settings.terminalThemeLabel("dracula")).toBe("Dracula");
  });

  it("prefers a target's override over the global default", async () => {
    const settings = await load();
    act(() => settings.settingsActions.setTermTheme("gruvbox-dark"));
    act(() => settings.settingsActions.setTermThemeOverride("a", "dracula"));

    const state = settings.getSettings();
    expect(settings.effectiveTerminalTheme(state, "a").id).toBe("dracula");
    // A target without an override still follows the global choice.
    expect(settings.effectiveTerminalTheme(state, "b").id).toBe("gruvbox-dark");
  });

  it("clearing an override deletes the key so the global default flows again", async () => {
    const settings = await load();
    act(() => settings.settingsActions.setTermThemeOverride("a", "dracula"));
    act(() => settings.settingsActions.setTermThemeOverride("a", null));

    expect(settings.getSettings().termThemeOverrides).toEqual({});

    act(() => settings.settingsActions.setTermTheme("gruvbox-light"));
    expect(settings.effectiveTerminalTheme(settings.getSettings(), "a").id).toBe("gruvbox-light");
  });

  it("prunes overrides for targets that no longer exist, keeping the live ones", async () => {
    const settings = await load();
    act(() => settings.settingsActions.setTermThemeOverride("a", "dracula"));
    act(() => settings.settingsActions.setTermThemeOverride("gone", "gruvbox-dark"));

    act(() => settings.settingsActions.pruneTermThemeOverrides(["a", "b"]));
    expect(settings.getSettings().termThemeOverrides).toEqual({ a: "dracula" });
  });

  it("pruning against an unchanged set leaves the state object identical", async () => {
    const settings = await load();
    act(() => settings.settingsActions.setTermThemeOverride("a", "dracula"));
    const before = settings.getSettings();

    act(() => settings.settingsActions.pruneTermThemeOverrides(["a", "b"]));
    expect(settings.getSettings()).toBe(before);
  });

  it("drops unknown persisted theme ids, globally and per target", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        termTheme: "solarized-dark-which-we-do-not-ship",
        termThemeOverrides: { a: "dracula", b: "nope", c: 7 },
      }),
    );
    const settings = await load();

    expect(settings.getSettings().termTheme).toBe("auto");
    expect(settings.getSettings().termThemeOverrides).toEqual({ a: "dracula" });
  });

  // "auto" is a valid preference but NOT a theme id: it must survive a reload
  // as the global choice, and must never be storable as a per-card override.
  it("keeps 'auto' as a persisted global choice, but not as an override", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ termTheme: "auto", termThemeOverrides: { a: "auto" } }),
    );
    const settings = await load();

    expect(settings.getSettings().termTheme).toBe("auto");
    expect(settings.getSettings().termThemeOverrides).toEqual({});
  });

  it("survives a non-object overrides blob", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ termThemeOverrides: "nope" }));
    const settings = await load();
    expect(settings.getSettings().termThemeOverrides).toEqual({});
  });
});

describe("useSettings", () => {
  it("re-renders subscribers when the theme changes", async () => {
    const settings = await load();
    const { result } = renderHook(() => settings.useSettings());

    expect(result.current.themeMode).toBe("system");
    act(() => settings.settingsActions.setThemeMode("dark"));
    expect(result.current.themeMode).toBe("dark");

    act(() => settings.settingsActions.setTermTheme("dracula"));
    expect(result.current.termTheme).toBe("dracula");
  });
});
