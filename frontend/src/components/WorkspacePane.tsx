// The right-hand terminal pane. Renders the empty state, or every ATTACHED
// terminal (each mounted for its lifetime so hidden ones stay connected),
// laid out as a single view (one visible) or a responsive grid (two-plus).

import { useMemo, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
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

  // dnd-kit reorder: `activeId` is the tile currently being dragged (drives the
  // floating DragOverlay ghost). Only the resulting order (`visible`) persists.
  const [activeId, setActiveId] = useState<string | null>(null);
  const sensors = useSensors(
    // A small move starts a drag, so plain clicks (buttons, focus) still work.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    // Touch: a short press-and-hold starts a drag, leaving taps/scroll intact.
    useSensor(TouchSensor, { activationConstraint: { delay: 180, tolerance: 8 } }),
    useSensor(KeyboardSensor),
  );

  // Render visible tiles in `visible` order (so the grid layout follows the
  // reorderable order + the number badges), then the hidden-but-attached cards
  // (kept mounted for their live connections; display:none, order irrelevant).
  const orderedTargets = useMemo(() => {
    const visibleSet = new Set(visible);
    const inGridOrder = visible
      .map((id) => targetsById.get(id))
      .filter((t): t is SessionTarget => t !== undefined);
    const hidden = attached
      .filter((id) => !visibleSet.has(id))
      .map((id) => targetsById.get(id))
      .filter((t): t is SessionTarget => t !== undefined);
    return [...inGridOrder, ...hidden];
  }, [visible, attached, targetsById]);

  if (orderedTargets.length === 0) {
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

  const toggleFullscreen = (id: string): void => {
    if (maximized === id) {
      workspace.restore();
    } else {
      workspace.maximize(id);
      // Request from within this click gesture so the browser allows it.
      requestBrowserFullscreen();
    }
  };

  // Reordering is possible only within a real grid (two-plus visible tiles).
  const reorderable = !maximized && mode === "grid" && visible.length > 1;
  const activeTarget = activeId ? targetsById.get(activeId) : undefined;

  const onDragStart = (e: DragStartEvent): void => setActiveId(String(e.active.id));
  const onDragEnd = (e: DragEndEvent): void => {
    const { active, over } = e;
    setActiveId(null);
    if (over && active.id !== over.id) {
      workspace.swapVisible(String(active.id), String(over.id));
    }
  };

  return (
    <main className="workspace" data-testid="workspace">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActiveId(null)}
      >
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
          {orderedTargets.map((target) => {
            const id = target.id;
            const isVisible = maximized ? id === maximized : visible.includes(id);
            const viewState =
              maximized === id ? "fullscreen" : mode === "grid" ? "grid" : "normal";
            return (
              <TerminalCard
                key={id}
                target={target}
                region={regionByKey.get(`${target.instance_type}::${target.instance_name}`)}
                mode={mode}
                isVisible={isVisible}
                isFocused={focusedId === id}
                viewState={viewState}
                reorderEnabled={reorderable && isVisible}
                onClose={() => workspace.closeTerm(id)}
                onNormal={() => workspace.soloTile(id)}
                onGrid={canGrid ? workspace.backToGrid : undefined}
                onToggleFullscreen={() => toggleFullscreen(id)}
                onFocusRequest={() => workspace.setFocused(id)}
                onActivity={() => workspace.markUnread(id)}
                onEnded={() => onTerminalEnded(target)}
              />
            );
          })}
        </div>

        {/* Floating "window outline" ghost that follows the cursor while dragging
         * a grid tile. dropAnimation off — the tiles swap instantly on drop. */}
        <DragOverlay dropAnimation={null}>
          {activeTarget ? (
            <div className="terminal-drag-ghost" aria-hidden="true">
              <span className="terminal-drag-ghost-title">{activeTarget.project}</span>
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </main>
  );
}
