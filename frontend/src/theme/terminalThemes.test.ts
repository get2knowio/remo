import { describe, expect, it } from "vitest";
import {
  AUTO_DARK_THEME_ID,
  AUTO_LIGHT_THEME_ID,
  AUTO_TERMINAL_THEME,
  autoTerminalTheme,
  DEFAULT_TERMINAL_SELECTION,
  isTerminalThemeId,
  isTerminalThemeSelection,
  resolveTerminalTheme,
  TERMINAL_THEMES,
  type TerminalThemeColors,
} from "./terminalThemes";

const HEX = /^#[0-9a-fA-F]{6}$/;

/** WCAG relative luminance, then the standard contrast ratio. */
function luminance(hex: string): number {
  const channels = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// The 22 ITheme fields every palette must set. Listed literally rather than
// derived from a sample theme, so a field dropped from every palette at once
// still fails here.
const COLOR_KEYS: (keyof TerminalThemeColors)[] = [
  "background",
  "foreground",
  "cursor",
  "cursorAccent",
  "selectionBackground",
  "selectionForeground",
  "black",
  "red",
  "green",
  "yellow",
  "blue",
  "magenta",
  "cyan",
  "white",
  "brightBlack",
  "brightRed",
  "brightGreen",
  "brightYellow",
  "brightBlue",
  "brightMagenta",
  "brightCyan",
  "brightWhite",
];

describe("terminal themes", () => {
  it("offers eight themes with unique ids", () => {
    expect(TERMINAL_THEMES).toHaveLength(8);
    expect(new Set(TERMINAL_THEMES.map((t) => t.id)).size).toBe(8);
  });

  it("balances four dark and four light schemes", () => {
    const byVariant = (v: "dark" | "light") => TERMINAL_THEMES.filter((t) => t.variant === v);
    expect(byVariant("dark")).toHaveLength(4);
    expect(byVariant("light")).toHaveLength(4);
  });

  // The site-matched pair is what "auto" resolves to, so their variants must
  // line up with the site mode they stand in for.
  it("ships a site-matched pair, one per variant", () => {
    expect(autoTerminalTheme(true).id).toBe(AUTO_DARK_THEME_ID);
    expect(autoTerminalTheme(true).variant).toBe("dark");
    expect(autoTerminalTheme(false).id).toBe(AUTO_LIGHT_THEME_ID);
    expect(autoTerminalTheme(false).variant).toBe("light");
  });

  // No theme may print text that is invisible on its own background.
  //
  // Reported from real use twice: Solarized Light's white and Gruvbox Light's
  // black were literally the background colour (1.00:1), and Catppuccin Latte's
  // whites were near enough. Upstream really does assign background tones to
  // those slots — which is fine for a swatch and useless for text — so the
  // fixed values are a deliberate deviation this test defends.
  //
  // `black` is exempt in DARK themes only: sitting near the background is what
  // colour 0 is FOR there, and applications use it as a fill rather than as
  // text. In a LIGHT theme the same slot IS printed as text, so it must be
  // legible — which is exactly the Gruvbox Light bug.
  //
  // The floor is 2.0:1, well below the 3:1 the Remo pair holds to, because this
  // catches INVISIBLE rather than merely quiet. The data splits cleanly: the
  // worst canonical slot across all eight themes is Gruvbox Light's yellow at
  // 2.19, while the five broken ones were all <= 1.91. A floor of 2.0 sits in
  // that gap, so every upstream chromatic value stays untouched.
  //
  // `cursor` is exempt: it is a filled block, not text, and reads at ratios
  // that would be unusable for glyphs.
  it.each(TERMINAL_THEMES)("$label prints nothing invisible", (theme) => {
    const bg = theme.colors.background;
    for (const [slot, hex] of Object.entries(theme.colors)) {
      if (
        ["background", "cursor", "cursorAccent", "selectionBackground", "selectionForeground"].includes(
          slot,
        )
      ) {
        continue;
      }
      if (slot === "black" && theme.variant === "dark") {
        continue;
      }
      expect(contrast(hex, bg), `${theme.id}.${slot} ${hex} on ${bg}`).toBeGreaterThan(2.0);
    }
  });

  // Every colour the Remo pair can PRINT must be legible on its own
  // background. This is the regression guard for a real report: Remo Light
  // shipped `white` at #d5d8db and `brightWhite` at #fbfcfd — 1.27:1 and
  // 1.09:1 — so Claude Code's "Cooked for 9s" and "(shift+tab to cycle)" hints
  // were invisible, and bold made it worse, since xterm draws bold with the
  // BRIGHT colour.
  //
  // Scoped to the two themes we author: the third-party ports are faithful
  // copies and several of them would fail this (Gruvbox Light sets colour 0 to
  // its own background), which is theirs to define, not ours to "fix".
  //
  // `black` is exempt: sitting near the background is what colour 0 is FOR in a
  // dark theme, and apps use it as a background fill far more than as text.
  describe.each([
    ["remo-dark", "#040608"],
    ["remo-light", "#eff2f6"],
  ])("%s stays legible on its own background", (id, bg) => {
    const theme = TERMINAL_THEMES.find((t) => t.id === id)!;
    const printable = Object.entries(theme.colors).filter(
      ([slot]) =>
        !["background", "cursorAccent", "selectionBackground", "selectionForeground", "black"].includes(
          slot,
        ),
    );

    it.each(printable)("%s (%s) clears 3:1", (slot, hex) => {
      expect(contrast(hex, bg), `${id}.${slot} ${hex} on ${bg}`).toBeGreaterThanOrEqual(3);
    });

    it("keeps bold white stronger than normal white, not weaker", () => {
      // Bold is drawn with brightWhite; if it were the paler of the two, bold
      // text would recede exactly where it should stand out.
      expect(contrast(theme.colors.brightWhite, bg)).toBeGreaterThan(
        contrast(theme.colors.white, bg),
      );
    });
  });

  // The palette is a hand-derived snapshot of theme/tokens.css (it cannot
  // reference the tokens live), so pin the anchors that make it "matching":
  // the terminal background IS --bg-term, and the foreground IS --text.
  it("anchors the site-matched pair to the console's own surface colors", () => {
    expect(autoTerminalTheme(true).colors.background).toBe("#040608"); // --bg-term dark
    expect(autoTerminalTheme(true).colors.foreground).toBe("#e2e5e9"); // --text dark
    expect(autoTerminalTheme(false).colors.background).toBe("#eff2f6"); // --bg-term light
    expect(autoTerminalTheme(false).colors.foreground).toBe("#242930"); // --text light
  });

  // xterm.js's ITheme wants literal colors — no oklch(), no alpha. A palette
  // typo that lands here would show up as a silently mis-rendered terminal,
  // so pin the format.
  it.each(TERMINAL_THEMES)("$label declares all 22 colors as #rrggbb", (theme) => {
    for (const key of COLOR_KEYS) {
      expect(theme.colors[key], `${theme.id}.${key}`).toMatch(HEX);
    }
    expect(Object.keys(theme.colors).sort()).toEqual([...COLOR_KEYS].sort());
  });

  it("has non-empty labels", () => {
    for (const theme of TERMINAL_THEMES) {
      expect(theme.label.length).toBeGreaterThan(0);
    }
  });

  it("defaults to following the site theme", () => {
    expect(DEFAULT_TERMINAL_SELECTION).toBe(AUTO_TERMINAL_THEME);
  });

  describe("resolveTerminalTheme", () => {
    it("returns the requested theme regardless of site mode", () => {
      expect(resolveTerminalTheme("dracula", true).label).toBe("Dracula");
      expect(resolveTerminalTheme("dracula", false).label).toBe("Dracula");
    });

    it("follows the site mode for 'auto'", () => {
      expect(resolveTerminalTheme(AUTO_TERMINAL_THEME, true).id).toBe(AUTO_DARK_THEME_ID);
      expect(resolveTerminalTheme(AUTO_TERMINAL_THEME, false).id).toBe(AUTO_LIGHT_THEME_ID);
    });

    // An unknown id can only come from a newer bundle or a hand-edited store;
    // resolving it like "auto" can never strand a dark terminal on a light page.
    it("treats an unknown or missing id as 'auto'", () => {
      expect(resolveTerminalTheme("nord-but-invented", false).id).toBe(AUTO_LIGHT_THEME_ID);
      expect(resolveTerminalTheme(undefined, true).id).toBe(AUTO_DARK_THEME_ID);
    });
  });

  describe("isTerminalThemeSelection", () => {
    it("accepts 'auto' and every shipped id, and nothing else", () => {
      expect(isTerminalThemeSelection(AUTO_TERMINAL_THEME)).toBe(true);
      for (const theme of TERMINAL_THEMES) {
        expect(isTerminalThemeSelection(theme.id)).toBe(true);
      }
      expect(isTerminalThemeSelection("system")).toBe(false);
      expect(isTerminalThemeSelection(null)).toBe(false);
    });

    it("does not let 'auto' pass as a concrete theme id", () => {
      expect(isTerminalThemeId(AUTO_TERMINAL_THEME)).toBe(false);
    });
  });

  describe("isTerminalThemeId", () => {
    it("accepts every shipped id", () => {
      for (const theme of TERMINAL_THEMES) {
        expect(isTerminalThemeId(theme.id)).toBe(true);
      }
    });

    it("rejects non-ids", () => {
      expect(isTerminalThemeId("solarized-dark")).toBe(false);
      expect(isTerminalThemeId("")).toBe(false);
      expect(isTerminalThemeId(undefined)).toBe(false);
      expect(isTerminalThemeId(null)).toBe(false);
      expect(isTerminalThemeId(3)).toBe(false);
      expect(isTerminalThemeId({ id: "dracula" })).toBe(false);
    });
  });
});
