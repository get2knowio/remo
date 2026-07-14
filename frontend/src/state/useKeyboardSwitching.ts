// Keyboard-based focus cycling among open terminals (T048, FR-031, US3
// scenario 2). Kept as a small standalone hook (rather than inlined in
// Dashboard.tsx) so the shortcut wiring is easy to read/reason about on its
// own.
//
// Shortcut choice: Ctrl+Shift+ArrowLeft / Ctrl+Shift+ArrowRight, to cycle
// focus backward/forward through the currently-open terminals.
//
// Rationale (this needed a modifier combo unlikely to be wanted *inside* a
// shell, AND unlikely to collide with the browser chrome itself):
//   - Plain Alt+ArrowLeft/Right was rejected: most browsers bind it to
//     back/forward navigation history. A terminal surface is a plain
//     <div>/<canvas>, not a text <input>, so that browser-level binding is
//     not reliably suppressed just by focusing the terminal — it would
//     fight the user constantly.
//   - Ctrl+Tab/Ctrl+Shift+Tab was rejected: both are real browser-tab
//     switching shortcuts in Chrome/Firefox and cannot be intercepted from
//     page JS at all.
//   - Plain Ctrl+ArrowLeft/Right was rejected: many shells/readline bind
//     Ctrl+Left/Right to word-left/word-right, which is exactly the kind of
//     everyday shell muscle-memory this shortcut must not steal.
//   - Ctrl+Shift+ArrowLeft/Right is not a default global browser shortcut
//     and is not a default shell/readline binding either, so it's free for
//     the workspace to claim.
//
// Because every open `TerminalCard` stays mounted even when hidden (T046),
// a hidden/backgrounded terminal's own DOM node could in principle still be
// the keydown target. This listener attaches on `window` in the CAPTURE
// phase and calls `stopPropagation()` on a match, so the workspace-level
// shortcut always wins regardless of which element currently has focus.

import { useEffect } from "react";

export function useKeyboardSwitching(
  openTargetIds: string[],
  focusedTargetId: string | null,
  setFocused: (targetId: string | null) => void,
): void {
  useEffect(() => {
    if (openTargetIds.length === 0) {
      return undefined;
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (!event.ctrlKey || !event.shiftKey || event.altKey || event.metaKey) {
        return;
      }
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
        return;
      }
      if (openTargetIds.length === 0) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();

      const currentIndex = focusedTargetId ? openTargetIds.indexOf(focusedTargetId) : -1;
      const delta = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex =
        currentIndex === -1 ? 0 : (currentIndex + delta + openTargetIds.length) % openTargetIds.length;
      setFocused(openTargetIds[nextIndex]);
    }

    window.addEventListener("keydown", handleKeyDown, { capture: true });
    return () => window.removeEventListener("keydown", handleKeyDown, { capture: true });
  }, [openTargetIds, focusedTargetId, setFocused]);
}
