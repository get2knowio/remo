// RendererAdapter implementation wrapping `ghostty-web` (T040, US2, FR-036
// default renderer per spec decision #6 / research.md R6).
//
// Targets `ghostty-web@0.4.0` (per `frontend/package.json`). IMPORTANT: this
// package is not installed in this dev sandbox (no network access), so the
// method-name mapping below (`new Terminal()` / `.open(container)` /
// `.write()` / `.onData()` / `.resize()`) was written against ghostty-web's
// documented/README-described API, which advertises xterm.js-compatible
// method names/semantics. A follow-up smoke test once the package is
// actually installed should confirm the exact method names and disposable
// shapes used below (in particular `onData`/`onTitleChange`/
// `onSelectionChange` return types, and whether a native fit/measure method
// exists) and this file adjusted accordingly.
//
// `fit()` has no confirmed ghostty-web equivalent of xterm.js's
// `xterm-addon-fit`, so dimensions are computed here by measuring a hidden
// monospace probe character against the container's pixel size — a renderer-
// agnostic technique that works regardless of ghostty-web's internal API.

import { Terminal } from "ghostty-web";
import type { RendererAdapter, TerminalDimensions } from "./RendererAdapter";

/** Minimal shape of the disposable object xterm.js-compatible `on*` methods
 * are expected to return; ghostty-web's README describes the same
 * subscribe/dispose convention. */
interface Disposable {
  dispose(): void;
}

export class GhosttyRenderer implements RendererAdapter {
  private readonly terminal: Terminal;
  private container: HTMLElement | null = null;

  constructor() {
    this.terminal = new Terminal({
      cursorBlink: true,
      scrollback: 5000,
      fontFamily: "Menlo, Consolas, 'DejaVu Sans Mono', monospace",
      fontSize: 13,
    });
  }

  open(container: HTMLElement): void {
    this.container = container;
    this.terminal.open(container);
  }

  write(data: Uint8Array | string): void {
    this.terminal.write(data);
  }

  onData(handler: (data: Uint8Array | string) => void): () => void {
    const disposable = this.terminal.onData((data: string | Uint8Array) => handler(data)) as
      | Disposable
      | undefined;
    return () => disposable?.dispose();
  }

  fit(): TerminalDimensions {
    if (!this.container) {
      return { cols: this.terminal.cols, rows: this.terminal.rows };
    }

    const { cols, rows } = measureCellGrid(this.container);
    this.terminal.resize(cols, rows);
    return { cols, rows };
  }

  resize(cols: number, rows: number): void {
    this.terminal.resize(cols, rows);
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
 * addon/method (see file-level note).
 */
function measureCellGrid(container: HTMLElement): TerminalDimensions {
  const probe = document.createElement("span");
  probe.textContent = "M";
  probe.style.visibility = "hidden";
  probe.style.position = "absolute";
  probe.style.whiteSpace = "pre";
  probe.style.fontFamily = "Menlo, Consolas, 'DejaVu Sans Mono', monospace";
  probe.style.fontSize = "13px";
  container.appendChild(probe);
  const cellWidth = probe.getBoundingClientRect().width || 8;
  const cellHeight = probe.getBoundingClientRect().height || 17;
  container.removeChild(probe);

  const { width, height } = container.getBoundingClientRect();
  const cols = Math.max(1, Math.floor(width / cellWidth));
  const rows = Math.max(1, Math.floor(height / cellHeight));
  return { cols, rows };
}
