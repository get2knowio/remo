// RendererAdapter implementation wrapping `ghostty-web` (T040, US2, FR-036 /
// research.md R6). Selectable via Settings → Terminal engine; xterm.js is the
// default engine (see XtermRenderer.ts / defaultRenderer.ts).
//
// Targets `ghostty-web@0.4.0` (per `frontend/package.json`). The method-name
// mapping below (`new Terminal()` / `.open(container)` / `.write()` /
// `.onData()` / `.resize()`) follows ghostty-web's documented xterm.js-
// compatible API. `fit()` measures a hidden monospace probe against the
// container (ghostty-web has no confirmed fit addon) — using the CURRENTLY
// configured font/size so the cell grid stays correct after a font change.

import { Terminal } from "ghostty-web";
import { inputForKeyEvent } from "./keymap";
import type {
  RendererAdapter,
  TerminalDimensions,
  TerminalFontOptions,
} from "./RendererAdapter";

/** Minimal shape of the disposable object xterm.js-compatible `on*` methods
 * are expected to return; ghostty-web's README describes the same
 * subscribe/dispose convention. */
interface Disposable {
  dispose(): void;
}

const DEFAULT_FONT: TerminalFontOptions = {
  fontFamily: "Menlo, Consolas, 'DejaVu Sans Mono', monospace",
  fontSize: 13,
  ligatures: false,
};

export class GhosttyRenderer implements RendererAdapter {
  private readonly terminal: Terminal;
  private container: HTMLElement | null = null;
  private font: TerminalFontOptions;
  /** Input subscribers — see XtermRenderer for the rationale (fan real input in,
   * plus synthesize Shift+Enter, through one path). */
  private readonly dataHandlers = new Set<(data: Uint8Array | string) => void>();

  constructor(font: TerminalFontOptions = DEFAULT_FONT) {
    this.font = font;
    this.terminal = new Terminal({
      cursorBlink: true,
      scrollback: 5000,
      fontFamily: font.fontFamily,
      fontSize: font.fontSize,
    });
    this.terminal.onData((data: string | Uint8Array) => this.emit(data));
    // Best-effort Shift+Enter → ESC+CR (newline), matching XtermRenderer. Guarded
    // because ghostty-web's custom-key-handler API is unconfirmed at 0.4.0; if
    // absent this is a no-op and Shift+Enter falls back to the engine default.
    const t = this.terminal as unknown as {
      attachCustomKeyEventHandler?: (h: (e: KeyboardEvent) => boolean) => void;
    };
    t.attachCustomKeyEventHandler?.((e) => this.handleKeyEvent(e));
  }

  private emit(data: Uint8Array | string): void {
    for (const handler of this.dataHandlers) {
      handler(data);
    }
  }

  private handleKeyEvent(e: KeyboardEvent): boolean {
    const seq = inputForKeyEvent(e);
    if (seq !== null) {
      e.preventDefault();
      this.emit(seq);
      return false;
    }
    return true;
  }

  open(container: HTMLElement): void {
    this.container = container;
    this.terminal.open(container);
  }

  write(data: Uint8Array | string): void {
    this.terminal.write(data);
  }

  onData(handler: (data: Uint8Array | string) => void): () => void {
    this.dataHandlers.add(handler);
    return () => this.dataHandlers.delete(handler);
  }

  fit(): TerminalDimensions {
    if (!this.container) {
      return { cols: this.terminal.cols, rows: this.terminal.rows };
    }

    const { cols, rows } = measureCellGrid(this.container, this.font);
    this.terminal.resize(cols, rows);
    return { cols, rows };
  }

  resize(cols: number, rows: number): void {
    this.terminal.resize(cols, rows);
  }

  applyFont(options: TerminalFontOptions): void {
    this.font = options;
    // ghostty-web exposes font config via its options bag (xterm.js-style).
    // Guard defensively in case a given build lacks a writable `options`.
    const opts = (this.terminal as unknown as { options?: Record<string, unknown> }).options;
    if (opts) {
      opts.fontFamily = options.fontFamily;
      opts.fontSize = options.fontSize;
    }
  }

  focus(): void {
    this.terminal.focus();
  }

  onTitleChange(handler: (title: string) => void): () => void {
    const disposable = this.terminal.onTitleChange((title: string) => handler(title)) as
      | Disposable
      | undefined;
    return () => disposable?.dispose();
  }

  onSelectionChange(handler: (hasSelection: boolean) => void): () => void {
    const disposable = this.terminal.onSelectionChange(() => {
      handler(this.terminal.hasSelection());
    }) as Disposable | undefined;
    return () => disposable?.dispose();
  }

  getSelection(): string | null {
    const selection = this.terminal.getSelection();
    return selection && selection.length > 0 ? selection : null;
  }

  dispose(): void {
    this.terminal.dispose();
  }
}

/**
 * Measures a hidden monospace probe character to derive the container's
 * available cols/rows, in the absence of a confirmed ghostty-web fit
 * addon/method. Uses the currently-configured font/size so the grid math
 * tracks live font changes.
 */
function measureCellGrid(
  container: HTMLElement,
  font: TerminalFontOptions,
): TerminalDimensions {
  const probe = document.createElement("span");
  probe.textContent = "M";
  probe.style.visibility = "hidden";
  probe.style.position = "absolute";
  probe.style.whiteSpace = "pre";
  probe.style.fontFamily = font.fontFamily;
  probe.style.fontSize = `${font.fontSize}px`;
  container.appendChild(probe);
  const cellWidth = probe.getBoundingClientRect().width || 8;
  const cellHeight = probe.getBoundingClientRect().height || 17;
  container.removeChild(probe);

  const { width, height } = container.getBoundingClientRect();
  const cols = Math.max(1, Math.floor(width / cellWidth));
  const rows = Math.max(1, Math.floor(height / cellHeight));
  return { cols, rows };
}
