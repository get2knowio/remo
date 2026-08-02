// Renderer construction.
//
// `xterm.js` is the console's terminal engine. It was once one of two — a
// `ghostty-web` WASM engine was selectable in Settings → Terminal engine — but
// that option is gone: its VT engine was pre-1.0, and it never actually worked
// under the service's `default-src 'self'` CSP (the package fetches its WASM
// from a `data:` URL, which `connect-src 'self'` blocks, so its init failed and
// every terminal silently fell back to xterm.js regardless). One well-tested
// engine is simpler to support than two, and this is the one in use.
//
// This module stays as the single construction point `TerminalCard` calls: it
// is the seam the component tests mock, and it keeps the card free of any
// direct dependency on a concrete renderer class (see `RendererAdapter`).

import type { RendererAdapter, TerminalFontOptions, TerminalThemeColors } from "./RendererAdapter";
import { XtermRenderer } from "./XtermRenderer";

/** Build the renderer `TerminalCard` uses. `font` and `theme` seed the initial
 * family/size/ligatures and color scheme from the settings store, so a
 * freshly-mounted terminal paints correctly on its first frame. */
export function createDefaultRenderer(
  font?: TerminalFontOptions,
  theme?: TerminalThemeColors,
): RendererAdapter {
  return new XtermRenderer(font, theme);
}
