// The right-hand terminal pane. Renders the empty state, or every ATTACHED
// terminal (each mounted for its lifetime so hidden ones stay connected),
// laid out as a single view (one visible) or a responsive grid (two-plus).

import { useMemo } from "react";
import type { SessionTarget } from "../api/client";
import { requestBrowserFullscreen } from "../lib/fullscreen";
import { useWorkspace } from "../state/workspace";
import { TerminalCard } from "./TerminalCard";
import "./WorkspacePane.css";

const REMO_ASCII = `   ____ ___ _ __ ___   ___
  | __// _ \\  _ \` _ \\ / _ \\
  | | |  __/ | | | | | (_) |
  |_|  \\___|_| |_| |_|\\___/`;

interface WorkspacePaneProps {
  /** id -> SessionTarget resolved from live discovery. */
  targetsById: Map<string, SessionTarget>;
  /** "type::name" -> registry region, for the terminal identity badge. */
  regionByKey: Map<string, string>;
  /** Re-run discovery for a target's instance (its terminal exited/closed). */
  onTerminalEnded: (target: SessionTarget) => void;
  narrow: boolean;
}

function gridColumns(visibleCount: number, narrow: boolean): number {
  if (narrow) {
    return 1;
  }
  if (visibleCount >= 5) {
    return 3;
  }
  if (visibleCount >= 2) {
    return 2;
  }
  return 1;
}

export function WorkspacePane({
  targetsById,
  regionByKey,
  onTerminalEnded,
  narrow,
}: WorkspacePaneProps): JSX.Element {
  const workspace = useWorkspace();
  const { attached, visible, focusedId, prevGrid, maximizedId } = workspace;

  // Only attached ids that still resolve to a live target get a card.
  const attachedTargets = useMemo(
    () =>
      attached
        .map((id) => targetsById.get(id))
        .filter((t): t is SessionTarget => t !== undefined),
    [attached, targetsById],
  );

  if (attachedTargets.length === 0) {
    return (
      <main className="workspace" data-testid="workspace">
        <div className="workspace-empty">
          <pre className="workspace-empty-art">{REMO_ASCII}</pre>
          <div className="workspace-empty-title">Select a session</div>
          <p className="workspace-empty-text">
            Click a session on the left to open it here. <kbd>⌘</kbd>-click (or <span>⊞</span>) a
            second one to view them side by side.
          </p>
          <p className="workspace-empty-hint">press 1–9 to jump · ? for shortcuts</p>
        </div>
      </main>
    );
  }

  // Fullscreen is an orthogonal overlay: it only takes effect when it still
  // resolves to an attached card. When active, that one card fills the pane and
  // the single↔grid layout underneath is left untouched (so exiting restores it).
  const maximized =
    maximizedId !== null && attached.includes(maximizedId) ? maximizedId : null;

  // Cards render as single (full-bleed) while a card is maximized; otherwise the
  // usual single↔grid split by how many are visible.
  const mode = maximized || visible.length <= 1 ? "single" : "grid";
  const cols = gridColumns(visible.length, narrow);
  const rows = Math.max(1, Math.ceil(visible.length / cols));
  // The Grid control is available when a grid can be shown — either the visible
  // set is already a grid (fullscreen opened over one) or a grid was remembered.
  const canGrid =
    visible.filter((id) => attached.includes(id)).length > 1 ||
    (prevGrid ?? []).filter((id) => attached.includes(id)).length > 1;

  // 1-based position for each visible id (grid tile number badge).
  const visibleIndex = new Map(visible.map((id, i) => [id, i + 1]));

  const toggleFullscreen = (id: string): void => {
    if (maximized === id) {
      workspace.restore();
    } else {
      workspace.maximize(id);
      // Request from within this click gesture so the browser allows it.
      requestBrowserFullscreen();
    }
  };

  return (
    <main className="workspace" data-testid="workspace">
      <div
        className={`workspace-body workspace-body--${mode}${maximized ? " workspace-body--maximized" : ""}`}
        style={
          mode === "grid"
            ? {
                gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
                gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
              }
            : undefined
        }
      >
        {attachedTargets.map((target) => {
          const isVisible = maximized ? target.id === maximized : visible.includes(target.id);
          const viewState = maximized === target.id ? "fullscreen" : mode === "grid" ? "grid" : "normal";
          return (
            <TerminalCard
              key={target.id}
              target={target}
              region={regionByKey.get(`${target.instance_type}::${target.instance_name}`)}
              mode={mode}
              isVisible={isVisible}
              isFocused={focusedId === target.id}
              num={visibleIndex.get(target.id)}
              viewState={viewState}
              onClose={() => workspace.closeTerm(target.id)}
              onSolo={mode === "grid" ? () => workspace.soloTile(target.id) : undefined}
              onNormal={() => workspace.soloTile(target.id)}
              onGrid={canGrid ? workspace.backToGrid : undefined}
              onToggleFullscreen={() => toggleFullscreen(target.id)}
              onFocusRequest={() => workspace.setFocused(target.id)}
              onActivity={() => workspace.markUnread(target.id)}
              onEnded={() => onTerminalEnded(target)}
            />
          );
        })}
      </div>
    </main>
  );
}
