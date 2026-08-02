// Settings store (console redesign).
//
// Same dependency-free `useSyncExternalStore` pattern as `workspace.ts` /
// `discovery.ts`. Holds display preferences for the console: site light/dark
// mode, accent color, terminal font/size/ligatures, terminal color theme (with
// per-target overrides), grid fit mode, rail width, and the family name of an
// uploaded Nerd Font (its bytes live in IndexedDB — see state/fonts.ts). All
// preferences are browser-local (FR-034); nothing is sent to the server.
//
// The terminal-affecting values are mirrored onto CSS custom properties on
// <html> (`--accent`, `--term-font`, `--term-size`, `--term-liga`) so plain
// CSS can react to them; the terminal renderers additionally read the derived
// TerminalFontOptions (see terminalFontOptions()) and TerminalThemeColors (see
// effectiveTerminalTheme()) to reconfigure live.
//
// Site mode is applied as `data-theme` on <html>, which theme/tokens.css turns
// into a pinned `color-scheme` — "system" removes the attribute entirely and
// lets the browser resolve `prefers-color-scheme` with no JS in the loop.

import { useSyncExternalStore } from "react";
import {
  AUTO_TERMINAL_THEME,
  DEFAULT_TERMINAL_SELECTION,
  isTerminalThemeId,
  isTerminalThemeSelection,
  resolveTerminalTheme,
  type TerminalTheme,
} from "../theme/terminalThemes";

const STORAGE_KEY = "remo-web:settings";

// The last entry is a mid-slate rather than a near-white: an off-white accent
// is invisible against the light theme's surfaces, and the accent is used for
// focus rings and borders in both modes.
export const ACCENT_OPTIONS = ["#38bdf8", "#4ade80", "#a78bfa", "#fb923c", "#94a3b8"] as const;

/** Site chrome theme. "system" follows the OS `prefers-color-scheme`. */
export type SiteThemeMode = "system" | "light" | "dark";

export interface SiteThemeOption {
  value: SiteThemeMode;
  label: string;
  icon: string;
  desc: string;
}

export const SITE_THEME_OPTIONS: SiteThemeOption[] = [
  {
    value: "system",
    label: "System",
    icon: "◐",
    desc: "Follow this device's appearance setting, switching as it does.",
  },
  { value: "light", label: "Light", icon: "☀", desc: "Always use the light console theme." },
  { value: "dark", label: "Dark", icon: "☾", desc: "Always use the dark console theme." },
];

const MEDIA_QUERY_DARK = "(prefers-color-scheme: dark)";

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
/** Focus-follows-mouse dwell (ms): 0 = instant, higher = calmer. */
export const MIN_FOCUS_DWELL_MS = 0;
export const MAX_FOCUS_DWELL_MS = 1000;
const DEFAULT_FOCUS_DWELL_MS = 220;

export interface SettingsState {
  /** Site chrome theme; "system" defers to the OS. */
  themeMode: SiteThemeMode;
  /** Live OS preference. NOT persisted and NOT what drives rendering (CSS's
   * `light-dark()` resolves "system" on its own) — it only lets the UI show
   * which theme "system" currently resolves to. */
  systemPrefersDark: boolean;
  accent: string;
  /** Terminal color scheme applied to every terminal without an override —
   * a theme id, or "auto" to track the site's light/dark mode. */
  termTheme: string;
  /** Per-target terminal theme overrides, keyed by `SessionTarget.id`. A target
   * that should follow the global default is ABSENT here — "Default" is a
   * deletion, never a stored id, so a later global change reaches it. */
  termThemeOverrides: Record<string, string>;
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
  /** Focus-follows-mouse dwell in ms (how long the pointer rests before focus). */
  focusDwellMs: number;
}

export interface TerminalFontOptions {
  fontFamily: string;
  fontSize: number;
  ligatures: boolean;
}

const DEFAULTS: SettingsState = {
  themeMode: "system",
  systemPrefersDark: true,
  accent: ACCENT_OPTIONS[0],
  termTheme: DEFAULT_TERMINAL_SELECTION,
  termThemeOverrides: {},
  termFontCss: FONT_OPTIONS[0].css,
  termSizeNum: 13,
  termLiga: true,
  gridFit: false,
  railWidth: DEFAULT_RAIL_WIDTH,
  railCollapsed: false,
  nerdFontName: null,
  renderer: "xterm",
  focusDwellMs: DEFAULT_FOCUS_DWELL_MS,
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

/** Current OS color preference; false wherever matchMedia is unavailable
 * (jsdom without a stub, SSR). Only ever used for display — see the
 * `systemPrefersDark` field comment. */
function prefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return DEFAULTS.systemPrefersDark;
  }
  return window.matchMedia(MEDIA_QUERY_DARK).matches;
}

/** Keep only entries whose value is a theme id this bundle knows. */
function sanitizeOverrides(raw: unknown): Record<string, string> {
  if (typeof raw !== "object" || raw === null) {
    return {};
  }
  const out: Record<string, string> = {};
  for (const [id, themeId] of Object.entries(raw as Record<string, unknown>)) {
    if (isTerminalThemeId(themeId)) {
      out[id] = themeId;
    }
  }
  return out;
}

/** Pristine defaults, with the one derived (non-persisted) field filled in. */
function freshDefaults(): SettingsState {
  return { ...DEFAULTS, systemPrefersDark: prefersDark() };
}

function loadPersisted(): SettingsState {
  if (typeof window === "undefined" || !window.localStorage) {
    return { ...DEFAULTS };
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return freshDefaults();
    }
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return freshDefaults();
    }
    const c = parsed as Partial<SettingsState>;
    return {
      themeMode:
        c.themeMode === "light" || c.themeMode === "dark" || c.themeMode === "system"
          ? c.themeMode
          : DEFAULTS.themeMode,
      systemPrefersDark: prefersDark(),
      accent: typeof c.accent === "string" ? c.accent : DEFAULTS.accent,
      termTheme: isTerminalThemeSelection(c.termTheme) ? c.termTheme : DEFAULTS.termTheme,
      termThemeOverrides: sanitizeOverrides(c.termThemeOverrides),
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
      focusDwellMs:
        typeof c.focusDwellMs === "number"
          ? clamp(Math.round(c.focusDwellMs), MIN_FOCUS_DWELL_MS, MAX_FOCUS_DWELL_MS)
          : DEFAULTS.focusDwellMs,
    };
  } catch (error) {
    console.error("settings: failed to restore from localStorage", error);
    return freshDefaults();
  }
}

function persist(s: SettingsState): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    // systemPrefersDark is observed from the OS on every load, never restored.
    const { systemPrefersDark: _derived, ...persistable } = s;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(persistable));
  } catch (error) {
    console.error("settings: failed to persist to localStorage", error);
  }
}

/** Push the site theme + terminal/accent vars onto <html> so CSS + renderers
 * see them. */
function applyToDom(s: SettingsState): void {
  if (typeof document === "undefined") {
    return;
  }
  const root = document.documentElement;
  // "system" means "say nothing and let :root's `color-scheme: light dark`
  // resolve prefers-color-scheme"; an explicit choice pins it (tokens.css).
  if (s.themeMode === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", s.themeMode);
  }
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

/** Apply persisted settings to the DOM once, at startup (before first paint),
 * and start tracking the OS color preference.
 *
 * Note the deliberate gap: a user whose explicit light/dark choice disagrees
 * with the OS can see one OS-colored frame before this runs. Closing it would
 * take an inline <script> in index.html, which the service's
 * `default-src 'self'` CSP forbids. */
export function initSettings(): void {
  applyToDom(state);
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return;
  }
  const media = window.matchMedia(MEDIA_QUERY_DARK);
  setState({ systemPrefersDark: media.matches });
  // Rendering under "system" needs no listener (CSS resolves it live); this
  // only keeps the top bar's icon/tooltip honest about what "system" means now.
  media.addEventListener("change", (e) => setState({ systemPrefersDark: e.matches }));
}

/** Which of the two palettes is actually showing right now. */
export function resolvedSiteTheme(s: SettingsState = state): "light" | "dark" {
  if (s.themeMode === "system") {
    return s.systemPrefersDark ? "dark" : "light";
  }
  return s.themeMode;
}

/** The terminal theme for `targetId`: its override if it has one, else the
 * global choice. "auto" (the default) — and any unrecognized id — resolves to
 * the site-matched theme for whichever mode is showing, so the terminal tracks
 * the console until the user picks something specific. */
export function effectiveTerminalTheme(
  s: SettingsState = state,
  targetId?: string,
): TerminalTheme {
  const override = targetId === undefined ? undefined : s.termThemeOverrides[targetId];
  return resolveTerminalTheme(override ?? s.termTheme, resolvedSiteTheme(s) === "dark");
}

/** How a terminal-theme preference reads in the UI. "auto" names the theme it
 * currently resolves to, so the label is never a dead end. */
export function terminalThemeLabel(selection: string, s: SettingsState = state): string {
  const theme = resolveTerminalTheme(selection, resolvedSiteTheme(s) === "dark");
  return selection === AUTO_TERMINAL_THEME ? `Follow site theme — ${theme.label}` : theme.label;
}

export function getSettings(): SettingsState {
  return state;
}

export function terminalFontOptions(s: SettingsState = state): TerminalFontOptions {
  return { fontFamily: s.termFontCss, fontSize: s.termSizeNum, ligatures: s.termLiga };
}

export const settingsActions = {
  setThemeMode: (themeMode: SiteThemeMode) => setState({ themeMode }),
  /** Top-bar toggle: system → light → dark → system. */
  cycleThemeMode: () => {
    const order: SiteThemeMode[] = ["system", "light", "dark"];
    const next = order[(order.indexOf(state.themeMode) + 1) % order.length];
    setState({ themeMode: next });
  },
  setTermTheme: (termTheme: string) => setState({ termTheme }),
  /** Set (or, with `null`, clear) one target's terminal-theme override.
   * Clearing DELETES the key rather than storing the current global id, so the
   * target keeps following the global default when that later changes. */
  setTermThemeOverride: (targetId: string, themeId: string | null) => {
    const next = { ...state.termThemeOverrides };
    if (themeId === null) {
      delete next[targetId];
    } else {
      next[targetId] = themeId;
    }
    setState({ termThemeOverrides: next });
  },
  /** Drop overrides for targets that no longer exist, so the stored map can't
   * grow without bound. Callers must only pass a successful, non-empty
   * discovery result — pruning against a failed snapshot would wipe live
   * preferences. */
  pruneTermThemeOverrides: (liveIds: readonly string[]) => {
    const live = new Set(liveIds);
    const entries = Object.entries(state.termThemeOverrides).filter(([id]) => live.has(id));
    if (entries.length === Object.keys(state.termThemeOverrides).length) {
      return; // nothing to drop — don't churn the store
    }
    setState({ termThemeOverrides: Object.fromEntries(entries) });
  },
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
  setFocusDwell: (focusDwellMs: number) =>
    setState({
      focusDwellMs: clamp(Math.round(focusDwellMs), MIN_FOCUS_DWELL_MS, MAX_FOCUS_DWELL_MS),
    }),
};

export function useSettings(): SettingsState {
  return useSyncExternalStore(subscribe, getSnapshot);
}
