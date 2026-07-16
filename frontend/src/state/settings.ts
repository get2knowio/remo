// Settings store (console redesign).
//
// Same dependency-free `useSyncExternalStore` pattern as `workspace.ts` /
// `discovery.ts`. Holds display preferences for the console: accent color,
// terminal font/size/ligatures, grid fit mode, rail width, and the family
// name of an uploaded Nerd Font (its bytes live in IndexedDB — see
// state/fonts.ts). All preferences are browser-local (FR-034); nothing is
// sent to the server.
//
// The terminal-affecting values are mirrored onto CSS custom properties on
// <html> (`--accent`, `--term-font`, `--term-size`, `--term-liga`) so plain
// CSS can react to them; the terminal renderers additionally read the derived
// TerminalFontOptions (see terminalFontOptions()) to reconfigure live.

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "remo-web:settings";

export const ACCENT_OPTIONS = ["#38bdf8", "#4ade80", "#a78bfa", "#fb923c", "#e5e7eb"] as const;

/** Which browser terminal engine backs each terminal. xterm.js is the stable
 * default; ghostty-web is the opt-in WASM engine (falls back to xterm if its
 * one-time init failed — see terminal/defaultRenderer.ts). */
export type RendererChoice = "xterm" | "ghostty";

export interface RendererOption {
  value: RendererChoice;
  label: string;
  tag: string;
  desc: string;
}

export const RENDERER_OPTIONS: RendererOption[] = [
  {
    value: "xterm",
    label: "xterm.js",
    tag: "Stable",
    desc: "The battle-tested emulator behind VS Code and many web IDEs. Recommended.",
  },
  {
    value: "ghostty",
    label: "ghostty-web",
    tag: "Experimental",
    desc: "Ghostty's WASM VT engine. Pre-1.0; falls back to xterm.js if it can't load.",
  },
];

export interface FontOption {
  label: string;
  css: string;
  tag: string;
  /** true when the font is bundled (@fontsource) and always available. */
  bundled: boolean;
}

// The bundled fonts are self-hosted (theme/fonts.ts); "bring your own" fonts
// rely on the OS having them installed, or an uploaded Nerd Font.
export const FONT_OPTIONS: FontOption[] = [
  { label: "IBM Plex Mono", css: "'IBM Plex Mono', monospace", tag: "Default", bundled: true },
  { label: "JetBrains Mono", css: "'JetBrains Mono', monospace", tag: "Ligatures", bundled: true },
  { label: "Fira Code", css: "'Fira Code', monospace", tag: "Ligatures", bundled: true },
  { label: "Source Code Pro", css: "'Source Code Pro', monospace", tag: "Clean", bundled: true },
  { label: "Hack", css: "'Hack', monospace", tag: "Bring your own", bundled: false },
  { label: "Cascadia Code", css: "'Cascadia Code', monospace", tag: "Bring your own", bundled: false },
];

export const MIN_TERM_SIZE = 11;
export const MAX_TERM_SIZE = 18;
export const MIN_RAIL_WIDTH = 262;
export const MAX_RAIL_WIDTH = 520;
const DEFAULT_RAIL_WIDTH = 320;

export interface SettingsState {
  accent: string;
  termFontCss: string;
  termSizeNum: number;
  termLiga: boolean;
  /** Scale each grid terminal to fit (true) vs keep font fixed + clip (false). */
  gridFit: boolean;
  railWidth: number;
  railCollapsed: boolean;
  /** Family name of the currently-registered uploaded Nerd Font, if any. */
  nerdFontName: string | null;
  /** Browser terminal engine to back each terminal. */
  renderer: RendererChoice;
}

export interface TerminalFontOptions {
  fontFamily: string;
  fontSize: number;
  ligatures: boolean;
}

const DEFAULTS: SettingsState = {
  accent: ACCENT_OPTIONS[0],
  termFontCss: FONT_OPTIONS[0].css,
  termSizeNum: 13,
  termLiga: true,
  gridFit: false,
  railWidth: DEFAULT_RAIL_WIDTH,
  railCollapsed: false,
  nerdFontName: null,
  renderer: "xterm",
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

function loadPersisted(): SettingsState {
  if (typeof window === "undefined" || !window.localStorage) {
    return { ...DEFAULTS };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { ...DEFAULTS };
    }
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return { ...DEFAULTS };
    }
    const c = parsed as Partial<SettingsState>;
    return {
      accent: typeof c.accent === "string" ? c.accent : DEFAULTS.accent,
      termFontCss: typeof c.termFontCss === "string" ? c.termFontCss : DEFAULTS.termFontCss,
      termSizeNum:
        typeof c.termSizeNum === "number"
          ? clamp(Math.round(c.termSizeNum), MIN_TERM_SIZE, MAX_TERM_SIZE)
          : DEFAULTS.termSizeNum,
      termLiga: typeof c.termLiga === "boolean" ? c.termLiga : DEFAULTS.termLiga,
      gridFit: typeof c.gridFit === "boolean" ? c.gridFit : DEFAULTS.gridFit,
      railWidth:
        typeof c.railWidth === "number"
          ? clamp(Math.round(c.railWidth), MIN_RAIL_WIDTH, MAX_RAIL_WIDTH)
          : DEFAULTS.railWidth,
      railCollapsed: typeof c.railCollapsed === "boolean" ? c.railCollapsed : DEFAULTS.railCollapsed,
      nerdFontName: typeof c.nerdFontName === "string" ? c.nerdFontName : null,
      renderer: c.renderer === "ghostty" || c.renderer === "xterm" ? c.renderer : DEFAULTS.renderer,
    };
  } catch (error) {
    console.error("settings: failed to restore from localStorage", error);
    return { ...DEFAULTS };
  }
}

function persist(s: SettingsState): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch (error) {
    console.error("settings: failed to persist to localStorage", error);
  }
}

/** Push the terminal/accent vars onto <html> so CSS + renderers see them. */
function applyToDom(s: SettingsState): void {
  if (typeof document === "undefined") {
    return;
  }
  const root = document.documentElement;
  root.style.setProperty("--accent", s.accent);
  root.style.setProperty("--term-font", s.termFontCss);
  root.style.setProperty("--term-size", `${s.termSizeNum}px`);
  root.style.setProperty("--term-liga", s.termLiga ? "normal" : "none");
}

let state: SettingsState = loadPersisted();

const listeners = new Set<() => void>();

function setState(partial: Partial<SettingsState>): void {
  state = { ...state, ...partial };
  persist(state);
  applyToDom(state);
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): SettingsState {
  return state;
}

/** Apply persisted settings to the DOM once, at startup (before first paint). */
export function initSettings(): void {
  applyToDom(state);
}

export function getSettings(): SettingsState {
  return state;
}

export function terminalFontOptions(s: SettingsState = state): TerminalFontOptions {
  return { fontFamily: s.termFontCss, fontSize: s.termSizeNum, ligatures: s.termLiga };
}

export const settingsActions = {
  setAccent: (accent: string) => setState({ accent }),
  setTermFont: (termFontCss: string) => setState({ termFontCss }),
  setTermSize: (termSizeNum: number) =>
    setState({ termSizeNum: clamp(Math.round(termSizeNum), MIN_TERM_SIZE, MAX_TERM_SIZE) }),
  toggleLiga: () => setState({ termLiga: !state.termLiga }),
  setGridFit: (gridFit: boolean) => setState({ gridFit }),
  setRailWidth: (railWidth: number) =>
    setState({ railWidth: clamp(Math.round(railWidth), MIN_RAIL_WIDTH, MAX_RAIL_WIDTH) }),
  toggleRailCollapsed: () => setState({ railCollapsed: !state.railCollapsed }),
  setNerdFontName: (nerdFontName: string | null) => setState({ nerdFontName }),
  setRenderer: (renderer: RendererChoice) => setState({ renderer }),
};

export function useSettings(): SettingsState {
  return useSyncExternalStore(subscribe, getSnapshot);
}
