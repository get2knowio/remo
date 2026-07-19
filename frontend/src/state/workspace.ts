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

interface PersistedWorkspaceState {
  attached: string[];
  visible: string[];
  focusedId: string | null;
}

interface WorkspaceState extends PersistedWorkspaceState {
  prevGrid: string[] | null;
  unread: string[];
  maximizedId: string | null;
}

function loadPersisted(): WorkspaceState {
  const fallback: WorkspaceState = {
    attached: [],
    visible: [],
    focusedId: null,
    prevGrid: null,
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
    return { attached, visible, focusedId, prevGrid: null, unread: [], maximizedId: null };
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
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toPersist));
  } catch (error) {
    console.error("workspace: failed to persist to localStorage", error);
  }
}

let state: WorkspaceState = loadPersisted();

const listeners = new Set<() => void>();

function setState(partial: Partial<WorkspaceState>): void {
  state = { ...state, ...partial };
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

/** Solo a grid tile into the single view, remembering the grid to return to. */
function soloTile(id: string): void {
  setState({
    prevGrid: state.visible.length > 1 ? state.visible : state.prevGrid,
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
    setState({ prevGrid: null, maximizedId: null });
    return;
  }
  const focusedId = grid.includes(state.focusedId ?? "") ? state.focusedId : grid[grid.length - 1];
  setState({ visible: grid, focusedId, prevGrid: null, maximizedId: null });
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
  setState({ attached, visible, focusedId: visible[0], prevGrid: null, maximizedId: null });
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
  selectOnly: (target: SessionTarget) => void;
  addSession: (target: SessionTarget) => void;
  soloTile: (id: string) => void;
  backToGrid: () => void;
  openMany: (targets: SessionTarget[]) => void;
  closeTerm: (id: string) => void;
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
    selectOnly,
    addSession,
    soloTile,
    backToGrid,
    openMany,
    closeTerm,
    maximize,
    restore,
    setFocused,
    markUnread,
  };
}
