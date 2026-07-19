// Remo-owned renderer adapter (T039, US2, FR-036/FR-037).
//
// Application code (TerminalConnection, TerminalCard) depends ONLY on this
// interface, never on `ghostty-web`/`xterm` classes directly — that is what
// makes it a decoupling adapter (spec decision: "do not couple application
// state directly to Ghostty Web classes"). Concrete implementations live in
// `XtermRenderer.ts` (the default engine — stable, battle-tested) and
// `GhosttyRenderer.ts` (opt-in via Settings, FR-036/SC-009). The user picks
// between them at runtime (`settings.renderer`); either satisfies this same
// interface, so the choice has no backend impact.
//
// Naming follows xterm.js's well-established public API vocabulary — the de
// facto reference shape for a browser terminal adapter — since both
// implementations must satisfy this same interface: `write`, `onData`,
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

/**
 * A Remo-owned adapter over a concrete browser terminal renderer
 * (`ghostty-web` or `xterm`). Implementations translate this interface's
 * calls into the underlying library's real API.
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
