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
import { WebLinksAddon } from "@xterm/addon-web-links";
import { ClipboardAddon, type IClipboardProvider } from "@xterm/addon-clipboard";
// xterm ships its own stylesheet; without it the terminal renders with broken
// cell sizing/positioning. Bundled here so it loads whenever this renderer is.
import "@xterm/xterm/css/xterm.css";
import { copyText } from "../lib/clipboard";
import { inputForKeyEvent, isCopyChord } from "./keymap";
import type {
  RendererAdapter,
  TerminalDimensions,
  TerminalFontOptions,
} from "./RendererAdapter";

// OSC 52 handling: WRITE lets a remote app (e.g. Claude Code's copy-on-select)
// push text to the browser clipboard; READ is denied so a remote app can never
// exfiltrate the operator's clipboard. Best-effort — an escape-sequence write
// has no user gesture, so some browsers may reject it.
const _osc52Provider: IClipboardProvider = {
  readText: async () => "",
  writeText: async (_selection, text) => {
    await copyText(text);
  },
};

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
  /** Desired ligature state; the addon is only actually (un)loaded after
   * open() since it registers a character joiner that needs an opened terminal. */
  private ligaturesEnabled = false;
  /** Input subscribers. We fan xterm's own onData into these AND synthesize
   * input for keys xterm doesn't distinguish (Shift+Enter), so both reach the
   * remote PTY through the same path. */
  private readonly dataHandlers = new Set<(data: Uint8Array | string) => void>();

  constructor(font: TerminalFontOptions = DEFAULT_FONT) {
    this.terminal = new Terminal({
      cursorBlink: true,
      convertEol: false,
      scrollback: 5000,
      fontFamily: font.fontFamily,
      fontSize: font.fontSize,
      // @xterm/addon-ligatures registers a character joiner, which lives behind
      // xterm's "proposed API" guard; without this the addon throws on load.
      allowProposedApi: true,
    });
    this.fitAddon = new FitAddon();
    this.terminal.loadAddon(this.fitAddon);
    // Real typed input flows through here to every subscriber.
    this.terminal.onData((data: string) => this.emit(data));
    // Distinguish Shift+Enter from Enter: xterm sends a plain CR for both, but
    // Claude Code and other TUIs expect Shift+Enter to insert a newline. Emit
    // ESC+CR (what `claude /terminal-setup` configures desktop terminals to
    // send) and suppress xterm's default CR so it isn't ALSO submitted.
    this.terminal.attachCustomKeyEventHandler((e) => this.handleKeyEvent(e));
    this.applyLigatures(font.ligatures);
  }

  private emit(data: Uint8Array | string): void {
    for (const handler of this.dataHandlers) {
      handler(data);
    }
  }

  private handleKeyEvent(e: KeyboardEvent): boolean {
    // Copy the selection on ⌘C / Ctrl+Shift+C — but only when something is
    // selected, so bare Ctrl+C still reaches the shell as SIGINT.
    if (isCopyChord(e) && this.terminal.hasSelection()) {
      e.preventDefault();
      void this.copySelection();
      return false;
    }
    const seq = inputForKeyEvent(e);
    if (seq !== null) {
      // preventDefault stops the browser from also inserting a newline into
      // xterm's hidden textarea (which would double-send via its input event);
      // it also cancels the keypress. Returning false suppresses xterm's own
      // handling of this keydown (its default CR that would submit the line).
      e.preventDefault();
      this.emit(seq);
      return false;
    }
    return true;
  }

  private applyLigatures(enabled: boolean): void {
    this.ligaturesEnabled = enabled;
    // @xterm/addon-ligatures calls registerCharacterJoiner in activate(), which
    // throws "Terminal must be opened first" if loaded pre-open. Defer to
    // open() when we're not attached yet; open() re-invokes with this state.
    if (!this.opened) {
      return;
    }
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
    // Now that the terminal is attached, load the ligatures addon if wanted.
    this.applyLigatures(this.ligaturesEnabled);
    // Clickable http(s) links — open in a new tab, severing opener access.
    this.terminal.loadAddon(
      new WebLinksAddon((_event, uri) => window.open(uri, "_blank", "noopener,noreferrer")),
    );
    // OSC 52 (remote-app clipboard writes, e.g. Claude Code copy-on-select).
    this.terminal.loadAddon(new ClipboardAddon(_osc52Provider));
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
    this.dataHandlers.add(handler);
    return () => this.dataHandlers.delete(handler);
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

  async copySelection(): Promise<boolean> {
    const selection = this.getSelection();
    if (!selection) {
      return false;
    }
    return copyText(selection);
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
