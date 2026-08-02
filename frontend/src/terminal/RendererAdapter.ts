// Remo-owned renderer adapter (T039, US2, FR-036/FR-037).
//
// Application code (TerminalConnection, TerminalCard) depends ONLY on this
// interface, never on a concrete renderer class — that is what makes it a
// decoupling adapter. `XtermRenderer.ts` is the sole implementation today (a
// second, `ghostty-web`-backed one was removed; see `defaultRenderer.ts`), but
// the seam earns its keep regardless: it is what the component tests mock, and
// it keeps the swap cost low if the engine is ever revisited.
//
// Naming follows xterm.js's well-established public API vocabulary — the de
// facto reference shape for a browser terminal adapter: `write`, `onData`,
// `resize`, `focus`, `onTitleChange`, `dispose` are all real xterm.js method
// names. Note the xterm.js semantics carried over here: `write()` pushes PTY
// output INTO the renderer, while `onData()` fires with bytes the user TYPED
// (keyboard/paste input to be forwarded to the remote PTY) — this is
// intentionally the inverse of what the names might suggest at a glance.
//
// This file is a pure interface: types + JSDoc only, zero implementation,
// zero imports from any renderer package.

/** Terminal grid dimensions in character cells. */
export interface TerminalDimensions {
  cols: number;
  rows: number;
}

/** Live-tunable terminal font settings (driven by state/settings.ts). */
export interface TerminalFontOptions {
  fontFamily: string;
  fontSize: number;
  ligatures: boolean;
}

/** A terminal color scheme, in xterm.js's `ITheme` shape. The concrete
 * palettes live in theme/terminalThemes.ts; this file stays import-free, so
 * the shape is re-declared structurally here. */
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

/**
 * A Remo-owned adapter over a concrete browser terminal renderer.
 * Implementations translate this interface's calls into the underlying
 * library's real API.
 */
export interface RendererAdapter {
  /**
   * Initializes the renderer and attaches it to `container`. Must be called
   * exactly once before any other method (except `dispose`).
   */
  open(container: HTMLElement): void;

  /** Writes PTY output bytes (or pre-decoded text) into the terminal. */
  write(data: Uint8Array | string): void;

  /**
   * Subscribes to user input events (keystrokes, paste, bracketed paste)
   * that should be forwarded to the remote PTY stdin. Returns an unsubscribe
   * function.
   */
  onData(handler: (data: Uint8Array | string) => void): () => void;

  /**
   * Resizes the renderer to fill its container (e.g. in response to a
   * `ResizeObserver` firing) and returns the resulting terminal dimensions
   * in cells. Callers typically forward the result to the server via a
   * `resize` control frame.
   */
  fit(): TerminalDimensions;

  /** Explicitly sets the terminal grid to `cols` x `rows`. */
  resize(cols: number, rows: number): void;

  /**
   * Applies new font settings (family/size/ligatures) to a live terminal.
   * Callers should `fit()` afterwards and forward the new dimensions, since a
   * font change alters the cell grid. Implementations must be safe to call
   * before `open()` (they cache the options for the eventual open).
   */
  applyFont(options: TerminalFontOptions): void;

  /**
   * Applies a new color scheme to a live terminal. Unlike `applyFont` this
   * does not change the cell grid, so no `fit()` is needed afterwards.
   * Implementations must be safe to call before `open()` (they cache the
   * colors for the eventual open).
   */
  applyTheme(colors: TerminalThemeColors): void;

  /** Moves keyboard focus into the terminal. */
  focus(): void;

  /**
   * Subscribes to renderer-reported title changes (e.g. OSC 0/2 escape
   * sequences from the remote shell). Returns an unsubscribe function.
   */
  onTitleChange(handler: (title: string) => void): () => void;

  /**
   * Subscribes to selection-state changes so callers can show/hide a "copy"
   * affordance. Returns an unsubscribe function.
   */
  onSelectionChange(handler: (hasSelection: boolean) => void): () => void;

  /** Returns the currently selected text, or `null` if nothing is selected. */
  getSelection(): string | null;

  /**
   * Copies the current selection to the system clipboard (best-effort; needs a
   * secure context). Returns true if there was a selection that was copied.
   */
  copySelection(): Promise<boolean>;

  /** Tears down the renderer and releases all resources/listeners. */
  dispose(): void;
}
