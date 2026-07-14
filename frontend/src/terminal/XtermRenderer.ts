// RendererAdapter implementation wrapping `xterm` (xterm.js), the well-known,
// stable terminal emulator library (T040, US2, FR-036 fallback renderer).
//
// Targets `xterm@^5` (per `frontend/package.json`) plus `xterm-addon-fit`
// (`^0.8`, added alongside this file — xterm.js has no built-in "fit
// container" behavior; the addon is the conventional, idiomatic way to
// measure a container and compute cols/rows, so it's added as a small,
// well-justified dependency rather than hand-rolling pixel measurement).
//
// This is the release-blocking fallback for `GhosttyRenderer` (SC-009): both
// implement the same `RendererAdapter`, so swapping the default in
// `TerminalCard.tsx` is a one-line change with no backend impact (FR-036).

import { Terminal } from "xterm";
import { FitAddon } from "xterm-addon-fit";
// xterm ships its own stylesheet; without it the terminal renders with broken
// cell sizing/positioning. Bundled here so it loads whenever this renderer is.
import "xterm/css/xterm.css";
import type { RendererAdapter, TerminalDimensions } from "./RendererAdapter";

export class XtermRenderer implements RendererAdapter {
  private readonly terminal: Terminal;
  private readonly fitAddon: FitAddon;
  private opened = false;

  constructor() {
    this.terminal = new Terminal({
      cursorBlink: true,
      convertEol: false,
      scrollback: 5000,
      fontFamily: "Menlo, Consolas, 'DejaVu Sans Mono', monospace",
      fontSize: 13,
    });
    this.fitAddon = new FitAddon();
    this.terminal.loadAddon(this.fitAddon);
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
