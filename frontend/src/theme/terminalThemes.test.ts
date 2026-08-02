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

// The 22 fields both engines' ITheme accepts. Listed literally rather than
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

  // The palette is a hand-derived snapshot of theme/tokens.css (it cannot
  // reference the tokens live), so pin the anchors that make it "matching":
  // the terminal background IS --bg-term, and the foreground IS --text.
  it("anchors the site-matched pair to the console's own surface colors", () => {
    expect(autoTerminalTheme(true).colors.background).toBe("#040608"); // --bg-term dark
    expect(autoTerminalTheme(true).colors.foreground).toBe("#e2e5e9"); // --text dark
    expect(autoTerminalTheme(false).colors.background).toBe("#eff2f6"); // --bg-term light
    expect(autoTerminalTheme(false).colors.foreground).toBe("#242930"); // --text light
  });

  // ghostty-web's color parser is the strict one: no `#rgb`, no alpha, no
  // oklch(). A palette typo that lands here would show up as a silently
  // mis-rendered terminal, so pin the format.
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
