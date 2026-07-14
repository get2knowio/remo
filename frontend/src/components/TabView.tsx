// Tab / focused layout for the terminal workspace (T046, US3, FR-031).
//
// Renders EVERY open `TerminalCard`, always mounted, and uses CSS
// (`display: none`) — never conditional unmounting — to hide the
// non-focused ones. This is the load-bearing detail for US3 scenario 3
// ("hidden terminals remain connected"): unmounting would tear down each
// card's `TerminalConnection`/WebSocket, which is exactly what must NOT
// happen when a terminal is merely not the one currently in view.
//
// `layoutMode: "tabs"` vs `"focused"` (workspace.ts) are intentionally the
// SAME underlying rendering — one visible card at a time, all mounted —
// differing only in chrome: `"tabs"` shows a clickable tab bar (mouse-driven
// switching, one click per target), `"focused"` hides that bar for a
// minimal, keyboard-driven view (T048's Ctrl+Shift+Arrow cycling — see
// `state/useKeyboardSwitching.ts` — still works in either mode, since both
// drive the same `focusedTargetId`). Keeping one component with a
// `showTabBar` flag avoids duplicating the "all mounted, only one visible"
// logic across two near-identical files.

import type { SessionTarget } from "../api/client";
import { TerminalCard } from "./TerminalCard";
import "./TabView.css";

interface TabViewProps {
  openTargets: SessionTarget[];
  focusedTargetId: string | null;
  onFocus: (targetId: string) => void;
  onClose: (targetId: string) => void;
  /** Show the clickable tab strip. Defaults to true ("tabs" mode); pass
   * false for "focused" mode's minimal chrome. */
  showTabBar?: boolean;
}

function tabLabel(target: SessionTarget): string {
  return `${target.instance_type} / ${target.instance_name} / ${target.project}`;
}

export function TabView({
  openTargets,
  focusedTargetId,
  onFocus,
  onClose,
  showTabBar = true,
}: TabViewProps): JSX.Element {
  // If the nominally-focused id doesn't resolve to a currently-open target
  // (e.g. it went stale, or nothing has been focused yet), fall back to the
  // first open target rather than rendering a blank pane.
  const effectiveFocusedId =
    focusedTargetId !== null && openTargets.some((target) => target.id === focusedTargetId)
      ? focusedTargetId
      : (openTargets[0]?.id ?? null);

  return (
    <div className="tab-view">
      {showTabBar && (
        <div className="tab-view-strip" role="tablist">
          {openTargets.map((target) => (
            <button
              key={target.id}
              type="button"
              role="tab"
              aria-selected={target.id === effectiveFocusedId}
              data-testid={`tab-${target.id}`}
              className={
                target.id === effectiveFocusedId
                  ? "tab-view-tab tab-view-tab--active"
                  : "tab-view-tab"
              }
              onClick={() => onFocus(target.id)}
            >
              <span className="tab-view-tab-label">{tabLabel(target)}</span>
              <span
                className="tab-view-tab-close"
                role="button"
                aria-label={`Close ${tabLabel(target)}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onClose(target.id);
                }}
              >
                ×
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="tab-view-panes">
        {openTargets.map((target) => (
          <div
            key={target.id}
            className="tab-view-pane"
            style={{ display: target.id === effectiveFocusedId ? "block" : "none" }}
          >
            <TerminalCard
              target={target}
              isFocused={target.id === effectiveFocusedId}
              onFocusRequest={() => onFocus(target.id)}
              onClose={() => onClose(target.id)}
            />
          </div>
        ))}
      </div>
    </div>
  );
}
