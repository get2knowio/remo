// Workspace store (console redesign, US3).
//
// Same dependency-free `useSyncExternalStore` pattern as `discovery.ts` /
// `settings.ts`. Models the rail-driven single↔grid workspace of the console
// mockup (docs/remo-web.html):
//
//   - `attached`  ids that have an open, connected terminal. A `TerminalCard`
//                 stays MOUNTED for every attached id (even when hidden) so its
//                 SSH connection + browser scrollback survive (US3 scenario 3).
//   - `visible`   the subset of `attached` currently shown in the pane. One
//                 visible → single view; two-plus → grid.
//   - `focusedId` the terminal that receives keyboard input / the focus ring.
//   - `prevGrid`  the grid remembered when soloing a tile, so "back to grid"
//                 can restore it.
//   - `unread`    attached-but-hidden ids that produced output since last seen
//                 (drives the rail's activity marker). Not persisted.
//   - `layout`    how the visible tiles are arranged: the uniform grid (the
//                 default), or a master/stack tiling where one tile holds a
//                 chosen side of the pane and the rest tile the leftover strip
//                 (the dwm/xmonad model). Persisted alongside `visible`, since a
//                 tiling is exactly as durable as the tile ORDER it decorates.
//   - `prevLayout`  the tiling remembered when soloing, so "back to grid" can
//                 restore it. Transient, mirroring `prevGrid`.
//   - `maximizedId`  the terminal currently shown fullscreen, or null. This is
//                 an ORTHOGONAL presentation overlay, NOT a third value on the
//                 single↔grid axis: it never mutates `visible`/`prevGrid`, so
//                 exiting fullscreen restores the exact single-or-grid layout
//                 underneath. Any explicit layout change (solo, grid, select,
//                 open-many) clears it. Not persisted (like `prevGrid`/`unread`);
//                 a reload returns to the normal shell.
//
// This store owns only IDs and layout intent; each `TerminalCard` still owns
// its own terminal_id/WebSocket lifecycle. Only `attached`/`visible`/
// `focusedId` are persisted to localStorage (FR-034); stale ids are tolerated
// (the consuming components join against `useDiscovery().targets` and skip any
// id that no longer resolves).

import { useSyncExternalStore } from "react";
import type { SessionTarget } from "../api/client";

const STORAGE_KEY = "remo-web:workspace";

export const MASTER_SIDES = ["left", "right", "top", "bottom"] as const;
export type MasterSide = (typeof MASTER_SIDES)[number];

/** How the visible tiles are arranged. `grid` is the uniform CSS grid the
 * console has always used, and is both the default and the fallback for every
 * invalid state. `master` is one tile holding `side` of the pane, with the rest
 * tiling the remainder.
 *
 * HOW MUCH of the pane it takes is deliberately NOT here: it is a display
 * preference (settings.masterSplit), so changing it re-flows the arrangement
 * you are looking at rather than only the next one you build. */
export type WorkspaceLayout =
  | { kind: "grid" }
  | { kind: "master"; id: string; side: MasterSide };

/** Shared instance so the uniform-grid case never allocates per write. */
const GRID_LAYOUT: WorkspaceLayout = { kind: "grid" };

interface PersistedWorkspaceState {
  attached: string[];
  visible: string[];
  focusedId: string | null;
  layout: WorkspaceLayout;
}

export interface WorkspaceState extends PersistedWorkspaceState {
  prevGrid: string[] | null;
  prevLayout: WorkspaceLayout | null;
  unread: string[];
  maximizedId: string | null;
}

/**
 * The layout's cross-field invariant, in ONE place.
 *
 * A master area only means anything while its tile is one of two-plus visible
 * tiles, so everything that shrinks or rebuilds `visible` has to be accounted
 * for. Rather than teach eight actions that rule — and forget it in the ninth —
 * `setState` runs this on every write and `loadPersisted` runs it on the
 * restored blob.
 *
 * A master that is no longer visible PROMOTES the head of the stack rather than
 * un-tiling: closing the master should move one tile, not reflow every survivor
 * (which resizes each remaining PTY and repaints any TUI). That is also what dwm
 * does. Returns its input unchanged when already valid, so unrelated actions
 * like `markUnread` never churn the layout reference.
 */
function normalizeLayout(layout: WorkspaceLayout, ids: string[]): WorkspaceLayout {
  if (layout.kind !== "master") {
    return GRID_LAYOUT;
  }
  if (ids.length < 2) {
    return GRID_LAYOUT;
  }
  const id = ids.includes(layout.id) ? layout.id : ids[0];
  return id === layout.id ? layout : { ...layout, id };
}

/** Shape-only validation of an untrusted persisted layout. Cross-field rules
 * belong to `normalizeLayout`, which runs after this. An unrecognized `kind`
 * (written by a newer build) degrades to the grid rather than throwing. */
function asLayout(v: unknown): WorkspaceLayout {
  if (typeof v !== "object" || v === null || Array.isArray(v)) {
    return GRID_LAYOUT;
  }
  const l = v as Partial<{ kind: unknown; id: unknown; side: unknown }>;
  if (l.kind !== "master") {
    return GRID_LAYOUT;
  }
  if (typeof l.id !== "string" || !MASTER_SIDES.includes(l.side as MasterSide)) {
    return GRID_LAYOUT;
  }
  // Any `fraction` written by 4.1.0 is dropped here: the split moved to
  // settings.masterSplit, and an ignored extra key needs no migration.
  return { kind: "master", id: l.id, side: l.side as MasterSide };
}

function loadPersisted(): WorkspaceState {
  const fallback: WorkspaceState = {
    attached: [],
    visible: [],
    focusedId: null,
    layout: GRID_LAYOUT,
    prevGrid: null,
    prevLayout: null,
    unread: [],
    maximizedId: null,
  };

  if (typeof window === "undefined" || !window.localStorage) {
    return fallback;
  }
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return fallback;
    }
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return fallback;
    }
    const c = parsed as Partial<PersistedWorkspaceState>;
    const asStrings = (v: unknown): string[] =>
      Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
    const attached = asStrings(c.attached);
    // visible must be a subset of attached; focus must be visible or null.
    const visible = asStrings(c.visible).filter((id) => attached.includes(id));
    const focusedId =
      typeof c.focusedId === "string" && visible.includes(c.focusedId) ? c.focusedId : (visible[0] ?? null);
    // Validated last, against the already-filtered `visible`.
    const layout = normalizeLayout(asLayout(c.layout), visible);
    return {
      attached,
      visible,
      focusedId,
      layout,
      prevGrid: null,
      prevLayout: null,
      unread: [],
      maximizedId: null,
    };
  } catch (error) {
    console.error("workspace: failed to restore from localStorage", error);
    return fallback;
  }
}

function persist(state: WorkspaceState): void {
  if (typeof window === "undefined" || !window.localStorage) {
    return;
  }
  try {
    const toPersist: PersistedWorkspaceState = {
      attached: state.attached,
      visible: state.visible,
      focusedId: state.focusedId,
      layout: state.layout,
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toPersist));
  } catch (error) {
    console.error("workspace: failed to persist to localStorage", error);
  }
}

let state: WorkspaceState = loadPersisted();

const listeners = new Set<() => void>();

function setState(partial: Partial<WorkspaceState>): void {
  const next = { ...state, ...partial };
  // The single write path for every action, so the layout invariant lives here
  // once instead of in each of them. See normalizeLayout.
  next.layout = normalizeLayout(next.layout, next.visible);
  state = next;
  persist(state);
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): WorkspaceState {
  return state;
}

/** Non-React accessor for the current workspace (mirrors settings.getSettings).
 * Used by the diagnostics snapshot, which must work outside a render. */
export function getWorkspace(): WorkspaceState {
  return state;
}

/** Ensure an id is attached (a TerminalCard will be rendered for it). */
function ensureAttached(id: string): string[] {
  return state.attached.includes(id) ? state.attached : [...state.attached, id];
}

function clearUnread(id: string): string[] {
  return state.unread.filter((u) => u !== id);
}

/** Open a target alone in the single view (click a rail row). */
function selectOnly(target: SessionTarget): void {
  setState({
    attached: ensureAttached(target.id),
    visible: [target.id],
    focusedId: target.id,
    prevGrid: null,
    unread: clearUnread(target.id),
    maximizedId: null,
  });
}

/** Toggle a target into/out of the visible grid (⌘/Ctrl-click a rail row). */
function addSession(target: SessionTarget): void {
  const id = target.id;
  const attached = ensureAttached(id);
  const isVisible = state.visible.includes(id);
  const visible = isVisible ? state.visible.filter((v) => v !== id) : [...state.visible, id];
  const focusedId = isVisible
    ? visible.includes(state.focusedId ?? "")
      ? state.focusedId
      : (visible[visible.length - 1] ?? null)
    : id;
  setState({ attached, visible, focusedId, unread: clearUnread(id), maximizedId: null });
}

/** Solo a grid tile into the single view, remembering the grid to return to.
 * Remembers the TILING under the same condition: Escape routes here
 * (useConsoleKeyboard), so without this one stray keypress would permanently
 * destroy a layout that took a drag to build. */
function soloTile(id: string): void {
  const wasGrid = state.visible.length > 1;
  setState({
    prevGrid: wasGrid ? state.visible : state.prevGrid,
    prevLayout: wasGrid ? state.layout : state.prevLayout,
    visible: [id],
    focusedId: id,
    unread: clearUnread(id),
    maximizedId: null,
  });
}

/** Show the grid, from any state. Prefers the currently-visible set when it is
 * already a grid (e.g. exiting fullscreen opened over a grid), else the grid
 * remembered from soloing a tile. Also clears any fullscreen overlay. */
function backToGrid(): void {
  const current = state.visible.filter((id) => state.attached.includes(id));
  const grid =
    current.length > 1 ? current : (state.prevGrid ?? []).filter((id) => state.attached.includes(id));
  if (grid.length <= 1) {
    setState({ prevGrid: null, prevLayout: null, maximizedId: null });
    return;
  }
  const focusedId = grid.includes(state.focusedId ?? "") ? state.focusedId : grid[grid.length - 1];
  // normalizeLayout validates the restored tiling against the restored grid.
  setState({
    visible: grid,
    layout: state.prevLayout ?? GRID_LAYOUT,
    focusedId,
    prevGrid: null,
    prevLayout: null,
    maximizedId: null,
  });
}

/** Open several targets at once as a grid (open-all). */
function openMany(targets: SessionTarget[]): void {
  if (targets.length === 0) {
    return;
  }
  const attached = [...state.attached];
  for (const t of targets) {
    if (!attached.includes(t.id)) {
      attached.push(t.id);
    }
  }
  const visible = targets.map((t) => t.id);
  // A full rebuild already discards the custom `visible` order; the tiling is
  // the same class of arrangement state, so it goes with it.
  setState({
    attached,
    visible,
    focusedId: visible[0],
    layout: GRID_LAYOUT,
    prevGrid: null,
    maximizedId: null,
  });
}

/** Swap two tiles' positions in the grid. The order lives in `visible` (which is
 * persisted), so a custom arrangement survives until the grid is rebuilt by a
 * solo/select/open-many. No-op if either id isn't currently visible. */
function swapVisible(a: string, b: string): void {
  const i = state.visible.indexOf(a);
  const j = state.visible.indexOf(b);
  if (i === -1 || j === -1 || i === j) {
    return;
  }
  const visible = [...state.visible];
  [visible[i], visible[j]] = [visible[j], visible[i]];
  // Mastership follows the SLOT, not the tile. Dragging the master onto a stack
  // tile otherwise swaps their array positions while `layout.id` still names the
  // master — so the tile the user dragged visibly would not move.
  let layout = state.layout;
  if (layout.kind === "master" && (layout.id === a || layout.id === b)) {
    layout = { ...layout, id: layout.id === a ? b : a };
  }
  setState({ visible, layout });
}

/** Give `id` the master area on `side`, tiling the rest into the remainder.
 * Normalised away by `normalizeLayout` when `id` isn't part of a two-plus grid,
 * so callers need no guard. */
function setMaster(id: string, side: MasterSide): void {
  // An explicit request naming a tile that isn't on screen is a caller bug, not
  // a tile disappearing — no-op rather than letting normalizeLayout's promotion
  // rule hand mastership to whatever happens to be first.
  if (!state.visible.includes(id)) {
    return;
  }
  setState({ layout: { kind: "master", id, side } });
}

/** Back to the uniform grid, leaving the tile ORDER untouched. */
function clearMaster(): void {
  setState({ layout: GRID_LAYOUT });
}

/** Close a terminal: reap it and re-pick focus / restore grid if soloed. */
function closeTerm(id: string): void {
  const attached = state.attached.filter((a) => a !== id);
  // If we were soloed (prevGrid set) and closing the solo tile, fall back to
  // the remembered grid; otherwise stay in the current visible set.
  const base = state.prevGrid && (state.prevGrid.includes(id) || state.visible.length <= 1)
    ? state.prevGrid
    : state.visible;
  const visible = (base ?? []).filter((v) => v !== id && attached.includes(v));
  const focusedId = visible.includes(state.focusedId ?? "")
    ? state.focusedId
    : (visible[visible.length - 1] ?? null);
  setState({
    attached,
    visible,
    focusedId,
    prevGrid: null,
    unread: clearUnread(id),
    // Closing the fullscreen terminal exits fullscreen (AppShell's effect then
    // leaves browser fullscreen); closing any other card leaves it untouched.
    maximizedId: state.maximizedId === id ? null : state.maximizedId,
  });
}

/** Show a terminal fullscreen (chrome hidden). Orthogonal overlay: leaves
 * `visible`/`prevGrid` intact so `restore()` returns to the layout underneath. */
function maximize(id: string): void {
  setState({
    attached: ensureAttached(id),
    focusedId: id,
    unread: clearUnread(id),
    maximizedId: id,
  });
}

/** Exit fullscreen, revealing the single-or-grid layout that was underneath. */
function restore(): void {
  if (state.maximizedId === null) {
    return;
  }
  setState({ maximizedId: null });
}

function setFocused(id: string | null): void {
  setState({ focusedId: id, unread: id ? clearUnread(id) : state.unread });
}

/** Flag new output on an attached-but-hidden terminal (rail activity dot). */
function markUnread(id: string): void {
  if (state.visible.includes(id) || state.unread.includes(id)) {
    return;
  }
  setState({ unread: [...state.unread, id] });
}

export interface UseWorkspaceResult {
  attached: string[];
  visible: string[];
  focusedId: string | null;
  prevGrid: string[] | null;
  unread: string[];
  maximizedId: string | null;
  layout: WorkspaceLayout;
  selectOnly: (target: SessionTarget) => void;
  addSession: (target: SessionTarget) => void;
  soloTile: (id: string) => void;
  backToGrid: () => void;
  openMany: (targets: SessionTarget[]) => void;
  closeTerm: (id: string) => void;
  swapVisible: (a: string, b: string) => void;
  setMaster: (id: string, side: MasterSide) => void;
  clearMaster: () => void;
  maximize: (id: string) => void;
  restore: () => void;
  setFocused: (id: string | null) => void;
  markUnread: (id: string) => void;
}

export function useWorkspace(): UseWorkspaceResult {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);
  return {
    attached: snapshot.attached,
    visible: snapshot.visible,
    focusedId: snapshot.focusedId,
    prevGrid: snapshot.prevGrid,
    unread: snapshot.unread,
    maximizedId: snapshot.maximizedId,
    layout: snapshot.layout,
    selectOnly,
    addSession,
    soloTile,
    backToGrid,
    openMany,
    closeTerm,
    swapVisible,
    setMaster,
    clearMaster,
    maximize,
    restore,
    setFocused,
    markUnread,
  };
}
