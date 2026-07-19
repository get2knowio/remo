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
