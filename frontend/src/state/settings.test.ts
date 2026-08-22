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

describe("terminal chrome toggle", () => {
  it("shows tile headers by default", async () => {
    const settings = await load();
    expect(settings.getSettings().showTileChrome).toBe(true);
  });

  it("toggles and round-trips through localStorage", async () => {
    const first = await load();
    act(() => first.settingsActions.toggleTileChrome());
    expect(first.getSettings().showTileChrome).toBe(false);

    const reloaded = await load();
    expect(reloaded.getSettings().showTileChrome).toBe(false);
  });

  it("falls back to showing them when the stored value is garbage", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ showTileChrome: "no" }));
    const settings = await load();
    expect(settings.getSettings().showTileChrome).toBe(true);
  });
});

describe("tiling split", () => {
  it("defaults to 40/60 in the master's favour", async () => {
    const settings = await load();
    expect(settings.getSettings().masterSplit).toBe(0.6);
  });

  it("accepts the offered splits and round-trips them", async () => {
    const first = await load();
    act(() => first.settingsActions.setMasterSplit(0.5));
    expect(first.getSettings().masterSplit).toBe(0.5);

    const reloaded = await load();
    expect(reloaded.getSettings().masterSplit).toBe(0.5);
  });

  // A value outside the offered set would render a layout that no menu item
  // matches, so it is refused at both the setter and the load.
  it("refuses a split that isn't offered", async () => {
    const settings = await load();
    act(() => settings.settingsActions.setMasterSplit(0.9));
    expect(settings.getSettings().masterSplit).toBe(0.6);

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ masterSplit: 0.9 }));
    const reloaded = await load();
    expect(reloaded.getSettings().masterSplit).toBe(0.6);
  });
});

describe("collapsed hosts", () => {
  it("defaults to none collapsed, including for legacy persisted JSON without the key", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ themeMode: "dark" }));
    const settings = await load();
    expect(settings.getSettings().collapsedHosts).toEqual([]);
  });

  it("toggles a host in and out and round-trips through localStorage", async () => {
    const first = await load();
    act(() => first.settingsActions.toggleHostCollapsed("i-1"));
    act(() => first.settingsActions.toggleHostCollapsed("i-2"));
    expect(first.getSettings().collapsedHosts).toEqual(["i-1", "i-2"]);

    act(() => first.settingsActions.toggleHostCollapsed("i-1"));
    expect(first.getSettings().collapsedHosts).toEqual(["i-2"]);

    const reloaded = await load();
    expect(reloaded.getSettings().collapsedHosts).toEqual(["i-2"]);
  });

  it("rejects garbage persisted values, keeping only string ids", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ collapsedHosts: "i-1" }));
    expect((await load()).getSettings().collapsedHosts).toEqual([]);

    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ collapsedHosts: ["i-1", 7, null, { id: "i-2" }] }),
    );
    expect((await load()).getSettings().collapsedHosts).toEqual(["i-1"]);
  });

  it("prunes collapse prefs for hosts that no longer exist, keeping live ones", async () => {
    const settings = await load();
    act(() => settings.settingsActions.toggleHostCollapsed("i-1"));
    act(() => settings.settingsActions.toggleHostCollapsed("gone"));

    act(() => settings.settingsActions.pruneCollapsedHosts(["i-1", "i-2"]));
    expect(settings.getSettings().collapsedHosts).toEqual(["i-1"]);
  });

  it("pruning collapse prefs against an unchanged set leaves the state object identical", async () => {
    const settings = await load();
    act(() => settings.settingsActions.toggleHostCollapsed("i-1"));
    const before = settings.getSettings();

    act(() => settings.settingsActions.pruneCollapsedHosts(["i-1", "i-2"]));
    expect(settings.getSettings()).toBe(before);
  });
});

describe("favorites", () => {
  const entry = { project: "remo", instanceType: "incus", instanceName: "lab/dev1" };

  it("defaults to none, including for legacy persisted JSON without the key", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ themeMode: "dark" }));
    const settings = await load();
    expect(settings.getSettings().favorites).toEqual({});
  });

  it("toggles a favorite on and off and round-trips through localStorage", async () => {
    const first = await load();
    act(() => first.settingsActions.toggleFavorite("t-1", entry));
    expect(first.getSettings().favorites).toEqual({ "t-1": entry });

    const reloaded = await load();
    expect(reloaded.getSettings().favorites).toEqual({ "t-1": entry });

    act(() => reloaded.settingsActions.toggleFavorite("t-1", entry));
    expect(reloaded.getSettings().favorites).toEqual({});
  });

  it("rejects garbage persisted values, keeping only complete entries", async () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ favorites: "nope" }));
    expect((await load()).getSettings().favorites).toEqual({});

    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        favorites: {
          "t-1": entry,
          "t-2": { project: "x", instanceType: "incus" },
          "t-3": { project: 7, instanceType: "incus", instanceName: "n" },
          "t-4": null,
        },
      }),
    );
    expect((await load()).getSettings().favorites).toEqual({ "t-1": entry });
  });

  // The load-bearing pruning case: an unreachable host's targets are absent
  // from discovery, and its favorites must survive that (rendered stale), or
  // one network blip would silently wipe the user's pins.
  it("keeps a favorite whose host is not reachable, even when its target is gone", async () => {
    const settings = await load();
    act(() => settings.settingsActions.toggleFavorite("t-1", entry));

    act(() =>
      settings.settingsActions.pruneFavorites(["other-target"], [settings.hostKey("aws", "box")]),
    );
    expect(settings.getSettings().favorites).toEqual({ "t-1": entry });
  });

  it("drops a favorite whose project was truly removed from a reachable host", async () => {
    const settings = await load();
    act(() => settings.settingsActions.toggleFavorite("t-1", entry));
    act(() =>
      settings.settingsActions.toggleFavorite("t-2", { ...entry, project: "keep" }),
    );

    act(() =>
      settings.settingsActions.pruneFavorites(["t-2"], [settings.hostKey("incus", "lab/dev1")]),
    );
    expect(settings.getSettings().favorites).toEqual({ "t-2": { ...entry, project: "keep" } });
  });

  it("pruning favorites against an unchanged set leaves the state object identical", async () => {
    const settings = await load();
    act(() => settings.settingsActions.toggleFavorite("t-1", entry));
    const before = settings.getSettings();

    act(() =>
      settings.settingsActions.pruneFavorites(["t-1"], [settings.hostKey("incus", "lab/dev1")]),
    );
    expect(settings.getSettings()).toBe(before);
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
