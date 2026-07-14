// Renderer selection (spec decision #6 + SC-009 fallback).
//
// `xterm.js` is the DEFAULT renderer: it's the stable, battle-tested emulator
// (VS Code et al.), which is what we want for day-to-day polish. `ghostty-web`
// is an opt-in choice (Settings → Terminal engine) — its WASM VT engine is
// pre-1.0. Users pick the engine via `settings.renderer`.
//
// `ghostty-web` requires an async `init()` (loads its WASM VT engine) to run
// exactly once before ANY `new Terminal()` is constructed. `RendererAdapter.
// open()` is synchronous and `GhosttyRenderer`'s constructor builds the
// terminal eagerly, so we perform that one-time init here at app startup (see
// `main.tsx`) BEFORE the first `TerminalCard` mounts — that way flipping to
// ghostty in Settings takes effect immediately without a reload.
//
// If `init()` fails (e.g. the same-origin WASM asset can't be fetched), we log
// it and force `XtermRenderer` even when ghostty was requested (FR-036/SC-009),
// so a terminal always works. `createDefaultRenderer(font, choice)` is what
// `TerminalCard` calls; it honors the requested engine, falling back to xterm
// when ghostty isn't available.

import { init as ghosttyInit } from "ghostty-web";
import type { RendererChoice } from "../state/settings";
import type { RendererAdapter, TerminalFontOptions } from "./RendererAdapter";
import { GhosttyRenderer } from "./GhosttyRenderer";
import { XtermRenderer } from "./XtermRenderer";

let ghosttyReady = false;
let initPromise: Promise<void> | null = null;

/** Load the ghostty-web WASM engine once. Never rejects: on failure it flips
 * the default to xterm.js instead of leaving the app unrenderable. */
export function initRenderers(): Promise<void> {
  if (initPromise) {
    return initPromise;
  }
  initPromise = ghosttyInit()
    .then(() => {
      ghosttyReady = true;
    })
    .catch((error: unknown) => {
      ghosttyReady = false;
      // eslint-disable-next-line no-console
      console.error(
        "[remo] ghostty-web init failed; falling back to xterm.js renderer.",
        error,
      );
    });
  return initPromise;
}

/** True once ghostty-web initialized successfully (opt-in engine is available). */
export function isGhosttyReady(): boolean {
  return ghosttyReady;
}

/** Build the renderer `TerminalCard` uses. `choice` is the user's selected
 * engine (default "xterm"); ghostty is used only when explicitly chosen AND its
 * WASM init succeeded, else we fall back to xterm. `font` seeds the initial
 * family/size/ligatures from the settings store. */
export function createDefaultRenderer(
  font?: TerminalFontOptions,
  choice: RendererChoice = "xterm",
): RendererAdapter {
  return choice === "ghostty" && ghosttyReady ? new GhosttyRenderer(font) : new XtermRenderer(font);
}
