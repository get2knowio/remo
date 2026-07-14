// The right-hand terminal pane. Renders the empty state, or every ATTACHED
// terminal (each mounted for its lifetime so hidden ones stay connected),
// laid out as a single view (one visible) or a responsive grid (two-plus).

import { useMemo } from "react";
import type { SessionTarget } from "../api/client";
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
  narrow,
}: WorkspacePaneProps): JSX.Element {
  const workspace = useWorkspace();
  const { attached, visible, focusedId, prevGrid } = workspace;

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

  const mode = visible.length <= 1 ? "single" : "grid";
  const cols = gridColumns(visible.length, narrow);
  const rows = Math.max(1, Math.ceil(visible.length / cols));
  const canBackToGrid = (prevGrid ?? []).filter((id) => attached.includes(id)).length > 1;

  // 1-based position for each visible id (grid tile number badge).
  const visibleIndex = new Map(visible.map((id, i) => [id, i + 1]));

  return (
    <main className="workspace" data-testid="workspace">
      <div
        className={`workspace-body workspace-body--${mode}`}
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
          const isVisible = visible.includes(target.id);
          return (
            <TerminalCard
              key={target.id}
              target={target}
              region={regionByKey.get(`${target.instance_type}::${target.instance_name}`)}
              mode={mode}
              isVisible={isVisible}
              isFocused={focusedId === target.id}
              num={visibleIndex.get(target.id)}
              onClose={() => workspace.closeTerm(target.id)}
              onSolo={mode === "grid" ? () => workspace.soloTile(target.id) : undefined}
              onBackToGrid={mode === "single" && canBackToGrid ? workspace.backToGrid : undefined}
              onFocusRequest={() => workspace.setFocused(target.id)}
              onActivity={() => workspace.markUnread(target.id)}
            />
          );
        })}
      </div>
    </main>
  );
}
