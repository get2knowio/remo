// Curated terminal color schemes for the browser terminals.
//
// Console-owned presentation data (no service shape involved), so this module
// is hand-written rather than generated — see the frontend Code Style note in
// CLAUDE.md. It is deliberately dependency-free: `state/settings.ts` imports it
// to validate persisted ids, and the settings store must stay importable
// without dragging in a renderer.
//
// Every color is plain `#rrggbb`: the renderer takes an xterm.js `ITheme`, so
// no `oklch()` and no alpha — an opaque selection color is what it expects.
// The uniform 6-digit form is also what makes the format test meaningful.
//
// Two of the schemes (Remo Dark/Light) are derived from the console's own
// tokens so the terminal matches the chrome — see the block comment on them.
// The rest are the official upstream ports: catppuccin.com/palette,
// draculatheme.com, github.com/morhetz/gruvbox, github.com/altercation/solarized.
//
// Three of those ports carry a flaw the originals share: in their LIGHT
// variants, a neutral slot that applications print as text is assigned a
// background tone, so that text is invisible on the theme's own background
// (Solarized Light's white/brightWhite, Gruvbox Light's black, Catppuccin
// Latte's white/brightWhite). Those five values are deliberately changed — each
// to another colour from the same palette — and the contrast floor in
// terminalThemes.test.ts keeps them that way. Every chromatic slot is untouched.

/** The subset of xterm.js's `ITheme` the console sets. */
export interface TerminalThemeColors {
  background: string;
  foreground: string;
  cursor: string;
  cursorAccent: string;
  selectionBackground: string;
  selectionForeground: string;
  black: string;
  red: string;
  green: string;
  yellow: string;
  blue: string;
  magenta: string;
  cyan: string;
  white: string;
  brightBlack: string;
  brightRed: string;
  brightGreen: string;
  brightYellow: string;
  brightBlue: string;
  brightMagenta: string;
  brightCyan: string;
  brightWhite: string;
}

export type TerminalThemeId =
  | "remo-dark"
  | "remo-light"
  | "catppuccin-mocha"
  | "dracula"
  | "gruvbox-dark"
  | "solarized-light"
  | "catppuccin-latte"
  | "gruvbox-light";

export interface TerminalTheme {
  id: TerminalThemeId;
  label: string;
  /** Whether the scheme is light-on-dark or dark-on-light. Shown as a tag in
   * Settings; a theme of either variant is selectable in either site mode. */
  variant: "dark" | "light";
  colors: TerminalThemeColors;
}

/** The pseudo-selection meaning "track the console's own light/dark mode".
 * It is not a theme id — it resolves to one at render time. */
export const AUTO_TERMINAL_THEME = "auto";

/** What a terminal-theme preference can be: a concrete theme, or "auto". */
export type TerminalThemeSelection = TerminalThemeId | typeof AUTO_TERMINAL_THEME;

/** Out of the box the terminal follows the console, so a light site doesn't
 * wrap a dark terminal (and vice versa) until the user picks for themselves. */
export const DEFAULT_TERMINAL_SELECTION: TerminalThemeSelection = AUTO_TERMINAL_THEME;

/** The pair "auto" chooses between — the two site-matched themes. */
export const AUTO_DARK_THEME_ID: TerminalThemeId = "remo-dark";
export const AUTO_LIGHT_THEME_ID: TerminalThemeId = "remo-light";

export const TERMINAL_THEMES: TerminalTheme[] = [
  // --- Site-matched pair (first: these are what "auto" resolves to) ---------
  //
  // Snapshots of theme/tokens.css, converted oklch -> sRGB by the browser's own
  // color pipeline, so a terminal sits flush with the chrome around it:
  //   background   <- --bg-term          foreground  <- --text
  //   ANSI 1-6     <- --danger/--ok/--warn/--info/--mag/--cyan
  //   black/white  <- the --bg-elev/--text-2/--border steps
  //   cursor       <- --info (NOT --accent: the accent is user-tunable, and a
  //                   static palette could not follow it)
  // These are SNAPSHOTS, not live references — a terminal palette has to be
  // literal #rrggbb (the renderer's ITheme takes neither oklch() nor var()).
  // Editing a token therefore does NOT update these; re-derive them by hand.
  //
  // One deliberate deviation: magenta is pulled to hue 330 (the token is 350),
  // because --danger sits at hue 25 and 350 left red and magenta too close to
  // tell apart in ls/diff output. Chrome keeps the original --mag.
  {
    id: "remo-dark",
    label: "Remo Dark",
    variant: "dark",
    colors: {
      background: "#040608",
      foreground: "#e2e5e9",
      cursor: "#74c2ee",
      cursorAccent: "#040608",
      selectionBackground: "#2c3136",
      selectionForeground: "#e2e5e9",
      black: "#15191e",
      red: "#fd736d",
      green: "#6ed889",
      yellow: "#eac25a",
      blue: "#74c2ee",
      magenta: "#e175d9",
      cyan: "#71d0d5",
      white: "#a0a5ab",
      brightBlack: "#5f646a",
      brightRed: "#ff9188",
      brightGreen: "#90f1a6",
      brightYellow: "#ffda7c",
      brightBlue: "#8ddcff",
      brightMagenta: "#f695ee",
      brightCyan: "#8beaef",
      brightWhite: "#e2e5e9",
    },
  },
  {
    id: "remo-light",
    label: "Remo Light",
    variant: "light",
    // On a light background the "bright" half goes DARKER, not lighter — the
    // same inversion Solarized Light uses, so bold text stays legible.
    //
    // That inversion has to include the WHITE pair, which is the trap Solarized
    // Light and Catppuccin Latte both fall into: a literal white `white` sits at
    // ~1.1:1 on a light background and simply is not there. Terminal apps print
    // colour 7/15 as TEXT constantly (Claude Code's "Cooked for 9s" and
    // "(shift+tab to cycle)" hints among them), so here they are the two darkest
    // neutrals — 6.3:1 and 13:1 — with brightWhite the stronger of the pair, so
    // bold still reads as emphasis. Reported from a live session; the contrast
    // floor is asserted in terminalThemes.test.ts.
    colors: {
      background: "#eff2f6",
      foreground: "#242930",
      cursor: "#007bb2",
      cursorAccent: "#eff2f6",
      selectionBackground: "#d5d8db",
      selectionForeground: "#242930",
      black: "#242930",
      red: "#c2272d",
      green: "#05893e",
      yellow: "#9a7400",
      blue: "#007bb2",
      magenta: "#ad36a7",
      cyan: "#008389",
      white: "#535960",
      brightBlack: "#6c727a",
      brightRed: "#a9000c",
      brightGreen: "#007026",
      brightYellow: "#825c00",
      brightBlue: "#0064a1",
      brightMagenta: "#950891",
      brightCyan: "#006b71",
      brightWhite: "#242930",
    },
  },
  // --- Curated third-party schemes -----------------------------------------
  {
    id: "catppuccin-mocha",
    label: "Catppuccin Mocha",
    variant: "dark",
    colors: {
      background: "#1e1e2e",
      foreground: "#cdd6f4",
      cursor: "#f5e0dc",
      cursorAccent: "#1e1e2e",
      selectionBackground: "#585b70",
      selectionForeground: "#cdd6f4",
      black: "#45475a",
      red: "#f38ba8",
      green: "#a6e3a1",
      yellow: "#f9e2af",
      blue: "#89b4fa",
      magenta: "#f5c2e7",
      cyan: "#94e2d5",
      white: "#bac2de",
      brightBlack: "#585b70",
      brightRed: "#f38ba8",
      brightGreen: "#a6e3a1",
      brightYellow: "#f9e2af",
      brightBlue: "#89b4fa",
      brightMagenta: "#f5c2e7",
      brightCyan: "#94e2d5",
      brightWhite: "#a6adc8",
    },
  },
  {
    id: "dracula",
    label: "Dracula",
    variant: "dark",
    colors: {
      background: "#282a36",
      foreground: "#f8f8f2",
      cursor: "#f8f8f2",
      cursorAccent: "#282a36",
      selectionBackground: "#44475a",
      selectionForeground: "#f8f8f2",
      black: "#21222c",
      red: "#ff5555",
      green: "#50fa7b",
      yellow: "#f1fa8c",
      blue: "#bd93f9",
      magenta: "#ff79c6",
      cyan: "#8be9fd",
      white: "#f8f8f2",
      brightBlack: "#6272a4",
      brightRed: "#ff6e6e",
      brightGreen: "#69ff94",
      brightYellow: "#ffffa5",
      brightBlue: "#d6acff",
      brightMagenta: "#ff92df",
      brightCyan: "#a4ffff",
      brightWhite: "#ffffff",
    },
  },
  {
    id: "gruvbox-dark",
    label: "Gruvbox Dark",
    variant: "dark",
    colors: {
      background: "#282828",
      foreground: "#ebdbb2",
      cursor: "#ebdbb2",
      cursorAccent: "#282828",
      selectionBackground: "#504945",
      selectionForeground: "#ebdbb2",
      black: "#282828",
      red: "#cc241d",
      green: "#98971a",
      yellow: "#d79921",
      blue: "#458588",
      magenta: "#b16286",
      cyan: "#689d6a",
      white: "#a89984",
      brightBlack: "#928374",
      brightRed: "#fb4934",
      brightGreen: "#b8bb26",
      brightYellow: "#fabd2f",
      brightBlue: "#83a598",
      brightMagenta: "#d3869b",
      brightCyan: "#8ec07c",
      brightWhite: "#ebdbb2",
    },
  },
  {
    // DELIBERATE DEVIATION from the upstream port, reported from real use.
    // Upstream assigns background tones to slots that applications print as
    // TEXT, so they were invisible on this theme's own background. Only those
    // neutrals move, and only to another colour from this same palette — the
    // chromatic slots stay canonical.
    id: "solarized-light",
    label: "Solarized Light",
    variant: "light",
    colors: {
      background: "#fdf6e3",
      foreground: "#657b83",
      cursor: "#657b83",
      cursorAccent: "#fdf6e3",
      selectionBackground: "#eee8d5",
      selectionForeground: "#657b83",
      black: "#073642",
      red: "#dc322f",
      green: "#859900",
      yellow: "#b58900",
      blue: "#268bd2",
      magenta: "#d33682",
      cyan: "#2aa198",
      white: "#657b83",
      brightBlack: "#002b36",
      brightRed: "#cb4b16",
      brightGreen: "#586e75",
      brightYellow: "#657b83",
      brightBlue: "#839496",
      brightMagenta: "#6c71c4",
      brightCyan: "#93a1a1",
      brightWhite: "#586e75",
    },
  },
  {
    // DELIBERATE DEVIATION from the upstream port, reported from real use.
    // Upstream assigns background tones to slots that applications print as
    // TEXT, so they were invisible on this theme's own background. Only those
    // neutrals move, and only to another colour from this same palette — the
    // chromatic slots stay canonical.
    id: "catppuccin-latte",
    label: "Catppuccin Latte",
    variant: "light",
    colors: {
      background: "#eff1f5",
      foreground: "#4c4f69",
      cursor: "#dc8a78",
      cursorAccent: "#eff1f5",
      selectionBackground: "#acb0be",
      selectionForeground: "#4c4f69",
      black: "#5c5f77",
      red: "#d20f39",
      green: "#40a02b",
      yellow: "#df8e1d",
      blue: "#1e66f5",
      magenta: "#ea76cb",
      cyan: "#179299",
      white: "#7c7f93",
      brightBlack: "#6c6f85",
      brightRed: "#d20f39",
      brightGreen: "#40a02b",
      brightYellow: "#df8e1d",
      brightBlue: "#1e66f5",
      brightMagenta: "#ea76cb",
      brightCyan: "#179299",
      brightWhite: "#4c4f69",
    },
  },
  {
    // DELIBERATE DEVIATION from the upstream port, reported from real use.
    // Upstream assigns background tones to slots that applications print as
    // TEXT, so they were invisible on this theme's own background. Only those
    // neutrals move, and only to another colour from this same palette — the
    // chromatic slots stay canonical.
    id: "gruvbox-light",
    label: "Gruvbox Light",
    variant: "light",
    colors: {
      background: "#fbf1c7",
      foreground: "#3c3836",
      cursor: "#3c3836",
      cursorAccent: "#fbf1c7",
      selectionBackground: "#d5c4a1",
      selectionForeground: "#3c3836",
      black: "#282828",
      red: "#cc241d",
      green: "#98971a",
      yellow: "#d79921",
      blue: "#458588",
      magenta: "#b16286",
      cyan: "#689d6a",
      white: "#7c6f64",
      brightBlack: "#928374",
      brightRed: "#9d0006",
      brightGreen: "#79740e",
      brightYellow: "#b57614",
      brightBlue: "#076678",
      brightMagenta: "#8f3f71",
      brightCyan: "#427b58",
      brightWhite: "#3c3836",
    },
  },
];

export function isTerminalThemeId(value: unknown): value is TerminalThemeId {
  return typeof value === "string" && TERMINAL_THEMES.some((t) => t.id === value);
}

/** A persisted preference is valid if it is a known theme id, or "auto". */
export function isTerminalThemeSelection(value: unknown): value is TerminalThemeSelection {
  return value === AUTO_TERMINAL_THEME || isTerminalThemeId(value);
}

/** The site-matched theme for the mode currently showing. */
export function autoTerminalTheme(siteIsDark: boolean): TerminalTheme {
  return themeById(siteIsDark ? AUTO_DARK_THEME_ID : AUTO_LIGHT_THEME_ID);
}

/**
 * The concrete theme a preference resolves to. "auto" — and anything
 * unrecognized, e.g. an id written by a newer bundle or hand-edited into
 * localStorage — follows the site's light/dark mode, which is the safe answer:
 * it can never leave a light console wrapped around a dark terminal.
 */
export function resolveTerminalTheme(
  selection: string | undefined,
  siteIsDark: boolean,
): TerminalTheme {
  const found = TERMINAL_THEMES.find((t) => t.id === selection);
  return found ?? autoTerminalTheme(siteIsDark);
}

function themeById(id: TerminalThemeId): TerminalTheme {
  return TERMINAL_THEMES.find((t) => t.id === id)!;
}
