// RendererAdapter implementation wrapping `xterm` (xterm.js), the well-known,
// stable terminal emulator library (T040, US2, FR-036). This is the DEFAULT
// terminal engine — battle-tested (VS Code et al.) and the surface we polish
// against day to day; `GhosttyRenderer` is the opt-in alternative (SC-009),
// selectable in Settings → Terminal engine.
//
// Targets the scoped `@xterm/*` packages: `@xterm/xterm@^5.5` plus
// `@xterm/addon-fit` (`^0.10`, container fit), `@xterm/addon-ligatures`
// (`^0.9`, programming ligatures — activated only when ligatures are enabled
// in Settings), and `@xterm/addon-webgl` (`^0.18`, GPU-accelerated rendering).
// Both renderers implement the same `RendererAdapter`, so the engine choice
// has no backend impact (FR-036).
//
// WebGL is loaded AFTER open() (the addon requires an attached terminal) and is
// strictly a rendering optimization: if the GPU context can't be created (e.g.
// a headless/software browser) or is later lost, we drop the addon and xterm
// falls back to its DOM/canvas renderer automatically — output is unaffected.

import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { LigaturesAddon } from "@xterm/addon-ligatures";
import { WebglAddon } from "@xterm/addon-webgl";
// xterm ships its own stylesheet; without it the terminal renders with broken
// cell sizing/positioning. Bundled here so it loads whenever this renderer is.
import "@xterm/xterm/css/xterm.css";
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
  private webglAddon: WebglAddon | null = null;
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
    this.enableWebgl();
  }

  // GPU-accelerated rendering. Must run after open(). Best-effort: a failed
  // context creation (or a later context loss) disposes the addon and lets
  // xterm's default DOM/canvas renderer take over — never a hard failure.
  private enableWebgl(): void {
    try {
      const addon = new WebglAddon();
      addon.onContextLoss(() => {
        addon.dispose();
        this.webglAddon = null;
      });
      this.terminal.loadAddon(addon);
      this.webglAddon = addon;
    } catch (error) {
      this.webglAddon = null;
      // eslint-disable-next-line no-console
      console.warn("[remo] WebGL terminal renderer unavailable; using DOM/canvas.", error);
    }
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
    // Dispose the WebGL addon before the terminal so its GPU context/canvas is
    // released cleanly (Terminal.dispose would also drop it, but ordering is
    // explicit here).
    this.webglAddon?.dispose();
    this.webglAddon = null;
    this.terminal.dispose();
  }
}
