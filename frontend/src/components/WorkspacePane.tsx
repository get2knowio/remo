// The right-hand terminal pane. Renders the empty state, or every ATTACHED
// terminal (each mounted for its lifetime so hidden ones stay connected),
// laid out as a single view (one visible) or a responsive grid (two-plus).

import { useEffect, useMemo, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  closestCenter,
  pointerWithin,
  useDroppable,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import type { SessionTarget } from "../api/client";
import { requestBrowserFullscreen } from "../lib/fullscreen";
import { MASTER_SIDES, useWorkspace, type MasterSide } from "../state/workspace";
import { dropIntent, nextMasterSide, paneLayout } from "./masterLayout";
import { TerminalCard } from "./TerminalCard";
import "./WorkspacePane.css";

const REMO_ASCII = `   ____ ___ _ __ ___   ___
  | __// _ \\  _ \` _ \\ / _ \\
  | | |  __/ | | | | | (_) |
  |_|  \\___|_| |_| |_|\\___/`;

/** How long hover-focus stays suppressed after tiles reflow. */
const LAYOUT_SETTLE_MS = 250;

/**
 * Edge zones win outright when the pointer is inside one — dragging to the
 * border is unambiguous intent, and `closestCenter` would otherwise keep
 * choosing whichever tile sits under the band (a thin strip's centre is almost
 * never the closest). `pointerWithin` returns [] when there are no pointer
 * coordinates, which is exactly the fallback we want: a KeyboardSensor drag
 * keeps today's swap-only behaviour.
 */
const snapAwareCollision: CollisionDetection = (args) => {
  const isZone = (c: (typeof args.droppableContainers)[number]): boolean =>
    c.data.current?.snapSide !== undefined;
  const zones = args.droppableContainers.filter(isZone);
  if (zones.length > 0) {
    const hits = pointerWithin({ ...args, droppableContainers: zones });
    if (hits.length > 0) {
      return hits;
    }
  }
  return closestCenter({
    ...args,
    droppableContainers: args.droppableContainers.filter((c) => !isZone(c)),
  });
};

/** Read a drop target as either an edge zone or a plain tile. Zones are told
 * apart by their `data` payload, never an id prefix — target ids come from the
 * server and must not be assumed prefix-free. */
function snapTargetOf(over: {
  id: string | number;
  data: { current?: { snapSide?: MasterSide } };
}): { id: string; snapSide?: MasterSide } {
  return { id: String(over.id), snapSide: over.data.current?.snapSide };
}

/** A drop target hugging one border of the pane.
 *
 * Deliberately `pointer-events: none` (see the CSS): dnd-kit resolves collisions
 * from measured RECTS, never DOM hit-testing, so a zone can overlay the pane
 * without stealing the pointer. That also leaves the rail's resize handle —
 * which overhangs 3px into this pane's left edge — owning pointerdown there. */
function SnapZone({ side, armed }: { side: MasterSide; armed: boolean }): JSX.Element {
  const { setNodeRef, isOver } = useDroppable({
    id: `remo-snap:${side}`,
    data: { snapSide: side },
  });
  return (
    <div
      ref={setNodeRef}
      aria-hidden="true"
      data-testid={`snap-zone-${side}`}
      className={`workspace-snap workspace-snap--${side}${armed ? " workspace-snap--armed" : ""}${
        isOver ? " workspace-snap--over" : ""
      }`}
    />
  );
}

interface WorkspacePaneProps {
  /** id -> SessionTarget resolved from live discovery. */
  targetsById: Map<string, SessionTarget>;
  /** "type::name" -> registry region, for the terminal identity badge. */
  regionByKey: Map<string, string>;
  /** Re-run discovery for a target's instance (its terminal exited/closed). */
  onTerminalEnded: (target: SessionTarget) => void;
  onTerminalStarted: (target: SessionTarget) => void;
  narrow: boolean;
}

export function WorkspacePane({
  targetsById,
  regionByKey,
  onTerminalEnded,
  onTerminalStarted,
  narrow,
}: WorkspacePaneProps): JSX.Element {
  const workspace = useWorkspace();
  const { attached, visible, focusedId, prevGrid, maximizedId, layout } = workspace;

  // A layout change reflows tiles under a stationary pointer, and the browser
  // then fires mouseenter for whatever landed there — arming focus-follows-mouse
  // and silently moving focus a beat later. Suppress hover-focus briefly after
  // any change. (WorkspacePane's activeId guard can't help: the drag has ended.)
  const [settling, setSettling] = useState(false);
  useEffect(() => {
    setSettling(true);
    const timer = setTimeout(() => setSettling(false), LAYOUT_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [layout, visible]);

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
  // `paneMode` is the single-vs-grid axis; `layout.kind` is the separate
  // uniform-vs-tiled axis. Two different "grid"s, hence the distinct name.
  const paneMode = maximized || visible.length <= 1 ? "single" : "grid";
  const pane = paneMode === "grid" ? paneLayout(layout, visible, narrow) : null;
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
  const reorderable = !maximized && paneMode === "grid" && visible.length > 1;
  const activeTarget = activeId ? targetsById.get(activeId) : undefined;

  const cycleTile = (id: string): void => {
    const side = nextMasterSide(layout, id);
    if (side) {
      workspace.setMaster(id, side);
    } else {
      workspace.clearMaster();
    }
  };

  const onDragStart = (e: DragStartEvent): void => setActiveId(String(e.active.id));
  const onDragEnd = (e: DragEndEvent): void => {
    const { active, over } = e;
    setActiveId(null);
    // The branchy part is a pure function: jsdom reports every element as 0x0,
    // so a real dnd-kit drop cannot be simulated and this is the only place the
    // decision can actually be tested. See masterLayout.test.ts.
    const intent = dropIntent(String(active.id), over ? snapTargetOf(over) : null);
    if (intent?.kind === "master") {
      workspace.setMaster(intent.id, intent.side);
    } else if (intent?.kind === "swap") {
      workspace.swapVisible(intent.a, intent.b);
    }
  };

  return (
    <main className="workspace" data-testid="workspace">
      <DndContext
        sensors={sensors}
        collisionDetection={snapAwareCollision}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActiveId(null)}
      >
        <div
          className={`workspace-body workspace-body--${paneMode}${maximized ? " workspace-body--maximized" : ""}`}
          style={pane?.container}
        >
          {/* Edge drop targets. Mounted whenever a reorder is possible, NOT on
           * activeId: dnd-kit measures droppable rects when the drag starts, so
           * a zone mounted inside onDragStart races that measurement. */}
          {reorderable &&
            MASTER_SIDES.map((side) => (
              <SnapZone key={side} side={side} armed={activeId !== null} />
            ))}
          {orderedTargets.map((target) => {
            const id = target.id;
            const isVisible = maximized ? id === maximized : visible.includes(id);
            const viewState =
              maximized === id ? "fullscreen" : paneMode === "grid" ? "grid" : "normal";
            return (
              <TerminalCard
                key={id}
                target={target}
                region={regionByKey.get(`${target.instance_type}::${target.instance_name}`)}
                mode={paneMode}
                gridArea={pane?.areaById.get(id)}
                masterSide={
                  layout.kind === "master" && layout.id === id ? layout.side : null
                }
                onCycleTile={
                  reorderable && isVisible ? () => cycleTile(id) : undefined
                }
                isVisible={isVisible}
                isFocused={focusedId === id}
                viewState={viewState}
                reorderEnabled={reorderable && isVisible}
                onClose={() => workspace.closeTerm(id)}
                onNormal={() => workspace.soloTile(id)}
                onGrid={
                  // In a TILED grid the ⊞ control flattens back to even tiles —
                  // it is otherwise dead UI in exactly the state where you want
                  // a way out, since cycling the ▦ control forward until it
                  // wraps is a poor answer to "how do I undo this".
                  paneMode === "grid" && !maximized
                    ? layout.kind === "master"
                      ? workspace.clearMaster
                      : undefined
                    : canGrid
                      ? workspace.backToGrid
                      : undefined
                }
                gridTitle={
                  paneMode === "grid" && !maximized && layout.kind === "master"
                    ? "Even out the grid"
                    : undefined
                }
                onToggleFullscreen={() => toggleFullscreen(id)}
                onFocusRequest={() => workspace.setFocused(id)}
                onHoverFocus={
                  paneMode === "grid" && !maximized && !settling
                    ? () => {
                        // Focus-follows-mouse: hovering a grid tile focuses it,
                        // but never while dragging (would fight the reorder).
                        if (!activeId && focusedId !== id) {
                          workspace.setFocused(id);
                        }
                      }
                    : undefined
                }
                onActivity={() => workspace.markUnread(id)}
                onEnded={() => onTerminalEnded(target)}
                onStarted={() => onTerminalStarted(target)}
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
