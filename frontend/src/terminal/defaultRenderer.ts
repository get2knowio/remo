// Default renderer selection (spec decision #6 + SC-009 fallback).
//
// `ghostty-web` is the intended default renderer, but it requires an async
// `init()` (loads its WASM VT engine) to run exactly once before ANY
// `new Terminal()` is constructed. `RendererAdapter.open()` is synchronous and
// `GhosttyRenderer`'s constructor builds the terminal eagerly, so we perform
// that one-time init here at app startup (see `main.tsx`) BEFORE the first
// `TerminalCard` mounts.
//
// If `init()` fails (e.g. the same-origin WASM asset can't be fetched), we log
// it and fall back to `XtermRenderer` — the stable, release-blocking fallback
// (FR-036/SC-009) — so a terminal still works. `createDefaultRenderer()` is
// what `TerminalCard` calls; it returns Ghostty once init succeeded, xterm
// otherwise.

import { init as ghosttyInit } from "ghostty-web";
import type { RendererAdapter } from "./RendererAdapter";
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

/** True once ghostty-web initialized successfully (default renderer is Ghostty). */
export function isGhosttyReady(): boolean {
  return ghosttyReady;
}

/** The renderer `TerminalCard` uses by default: Ghostty when ready, else xterm. */
export function createDefaultRenderer(): RendererAdapter {
  return ghosttyReady ? new GhosttyRenderer() : new XtermRenderer();
}
