// Console keyboard shortcuts (replaces the old Ctrl+Shift+Arrow cycling).
//
//   1–9            open that numbered session solo (single view)
//   ⌘/Ctrl/Shift 1–9  add/toggle that session in the grid
//   Esc            close an open overlay, else collapse the grid to the
//                  focused terminal
//   ?              toggle the shortcuts panel
//
// Ignored while typing in an <input>/<textarea> (e.g. the rail search).

import { useEffect } from "react";
import type { SessionTarget } from "../api/client";
import type { UseWorkspaceResult } from "./workspace";

export interface ConsoleKeyboardConfig {
  /** The numbered, openable targets in rail order (index 0 == "1"). */
  flatOpenable: SessionTarget[];
  workspace: UseWorkspaceResult;
  onToggleShortcuts: () => void;
  /** Close any open overlay; return true if one was actually closed. */
  onEscapeOverlay: () => boolean;
}

function isTypingTarget(target: EventTarget | null): boolean {
  const tag = (target as HTMLElement | null)?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA";
}

export function useConsoleKeyboard(config: ConsoleKeyboardConfig): void {
  const { flatOpenable, workspace, onToggleShortcuts, onEscapeOverlay } = config;

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent): void {
      if (isTypingTarget(e.target)) {
        return;
      }

      if (e.key === "?") {
        e.preventDefault();
        onToggleShortcuts();
        return;
      }

      if (e.key === "Escape") {
        if (onEscapeOverlay()) {
          return;
        }
        // Collapse a grid to just the focused terminal.
        if (workspace.visible.length > 1 && workspace.focusedId) {
          workspace.soloTile(workspace.focusedId);
        }
        return;
      }

      if (/^[1-9]$/.test(e.key)) {
        const target = flatOpenable[Number(e.key) - 1];
        if (!target) {
          return;
        }
        e.preventDefault();
        if (e.metaKey || e.ctrlKey || e.shiftKey) {
          workspace.addSession(target);
        } else {
          workspace.selectOnly(target);
        }
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [flatOpenable, workspace, onToggleShortcuts, onEscapeOverlay]);
}
