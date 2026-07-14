// RendererAdapter implementation wrapping `xterm` (xterm.js), the well-known,
// stable terminal emulator library (T040, US2, FR-036 fallback renderer).
//
// Targets `xterm@^5` plus `xterm-addon-fit` (`^0.8`, container fit) and
// `xterm-addon-ligatures` (`^0.6`, programming ligatures — activated only when
// ligatures are enabled in Settings). This is the release-blocking fallback
// for `GhosttyRenderer` (SC-009): both implement the same `RendererAdapter`,
// so swapping the default in `defaultRenderer.ts` is a one-line change with no
// backend impact (FR-036).

import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";
import { LigaturesAddon } from "xterm-addon-ligatures";
// xterm ships its own stylesheet; without it the terminal renders with broken
// cell sizing/positioning. Bundled here so it loads whenever this renderer is.
import "xterm/css/xterm.css";
import type {
  RendererAdapter,
  TerminalDimensions,
  TerminalFontOptions,
} from "./RendererAdapter";

const DEFAULT_FONT: TerminalFontOptions = {
  fontFamily: "Menlo, Consolas, 'DejaVu Sans Mono', monospace",
  fontSize: 13,
  ligatures: false,
};

export class XtermRenderer implements RendererAdapter {
  private readonly terminal: Terminal;
  private readonly fitAddon: FitAddon;
  private ligaturesAddon: LigaturesAddon | null = null;
  private opened = false;

  constructor(font: TerminalFontOptions = DEFAULT_FONT) {
    this.terminal = new Terminal({
      cursorBlink: true,
      convertEol: false,
      scrollback: 5000,
      fontFamily: font.fontFamily,
      fontSize: font.fontSize,
    });
    this.fitAddon = new FitAddon();
    this.terminal.loadAddon(this.fitAddon);
    this.applyLigatures(font.ligatures);
  }

  private applyLigatures(enabled: boolean): void {
    // The ligatures addon registers on the terminal; if loaded pre-open it
    // still takes effect once open() runs.
    if (enabled && !this.ligaturesAddon) {
      this.ligaturesAddon = new LigaturesAddon();
      this.terminal.loadAddon(this.ligaturesAddon);
    } else if (!enabled && this.ligaturesAddon) {
      this.ligaturesAddon.dispose();
      this.ligaturesAddon = null;
    }
  }

  open(container: HTMLElement): void {
    this.terminal.open(container);
    this.opened = true;
  }

  write(data: Uint8Array | string): void {
    this.terminal.write(data);
  }

  onData(handler: (data: Uint8Array | string) => void): () => void {
    const disposable = this.terminal.onData((data: string) => handler(data));
    return () => disposable.dispose();
  }

  fit(): TerminalDimensions {
    if (!this.opened) {
      return { cols: this.terminal.cols, rows: this.terminal.rows };
    }
    this.fitAddon.fit();
    return { cols: this.terminal.cols, rows: this.terminal.rows };
  }

  resize(cols: number, rows: number): void {
    this.terminal.resize(cols, rows);
  }

  applyFont(options: TerminalFontOptions): void {
    this.terminal.options.fontFamily = options.fontFamily;
    this.terminal.options.fontSize = options.fontSize;
    this.applyLigatures(options.ligatures);
  }

  focus(): void {
    this.terminal.focus();
  }

  onTitleChange(handler: (title: string) => void): () => void {
    const disposable = this.terminal.onTitleChange((title: string) => handler(title));
    return () => disposable.dispose();
  }

  onSelectionChange(handler: (hasSelection: boolean) => void): () => void {
    const disposable = this.terminal.onSelectionChange(() => {
      handler(this.terminal.hasSelection());
    });
    return () => disposable.dispose();
  }

  getSelection(): string | null {
    const selection = this.terminal.getSelection();
    return selection.length > 0 ? selection : null;
  }

  dispose(): void {
    this.terminal.dispose();
  }
}
