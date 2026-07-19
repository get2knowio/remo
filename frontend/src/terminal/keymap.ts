// Renderer-agnostic key mapping for keys the browser terminal engines don't
// distinguish on their own. Pure (no engine/DOM state) so it can be unit-tested
// directly and shared by XtermRenderer and GhosttyRenderer.

/**
 * The raw sequence to send to the remote PTY for a key event the renderer must
 * special-case, or `null` to let the engine handle the key normally.
 *
 * Currently only **Shift+Enter**: xterm/ghostty send a plain CR (submit) for
 * both Enter and Shift+Enter, but Claude Code and other TUIs expect Shift+Enter
 * to insert a newline. Desktop terminals (via `claude /terminal-setup`) send
 * ESC+CR for this; we mirror that. Only the `keydown` produces the sequence —
 * the caller `preventDefault()`s it, which also cancels the would-be keypress.
 */
export function inputForKeyEvent(e: KeyboardEvent): string | null {
  if (
    e.type === "keydown" &&
    e.key === "Enter" &&
    e.shiftKey &&
    !e.ctrlKey &&
    !e.altKey &&
    !e.metaKey
  ) {
    return "\x1b\r";
  }
  return null;
}

/**
 * Whether a key event is the **Ctrl+Shift+C** explicit-copy chord (Linux/Windows).
 * Bare `Ctrl+C` is excluded so it stays SIGINT. **⌘C on macOS is intentionally
 * NOT handled here** — it fires the browser's native `copy` event, which the
 * renderer handles via `clipboardData` (synchronous, and reliable in Safari,
 * unlike an async `navigator.clipboard.writeText()` off a preventDefault'd key).
 */
export function isCopyChord(e: KeyboardEvent): boolean {
  if (e.type !== "keydown" || (e.key !== "c" && e.key !== "C")) {
    return false;
  }
  return e.ctrlKey && e.shiftKey && !e.metaKey && !e.altKey;
}
