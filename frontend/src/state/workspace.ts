// Workspace store (T045, US3).
//
// Same dependency-free `useSyncExternalStore` pattern as `discovery.ts` (no
// Redux/Zustand) — a single module-level store shared by every
// `useWorkspace()` caller.
//
// Scope note (data-model.md "BrowserWorkspace", FR-034): a terminal is not
// "created" (POST /terminals) until a `TerminalCard` mounts and its internal
// `TerminalConnection` calls `createTerminal()`. This store does NOT own
// that lifecycle — it only tracks WHICH `SessionTarget`s (by `id`) are
// "open" (i.e. should have a `TerminalCard` rendered for them), their
// display order, and the layout/focus preferences. The actual terminal_id /
// WebSocket lifecycle stays fully encapsulated inside each `TerminalCard`.
//
// Persistence (FR-034): `openTargetIds` and `layoutMode` are persisted to
// `localStorage` only — "no server-side database is required for the MVP".
// `focusedTargetId` is persisted too (a small, harmless addition — it's not
// required by the spec, but restoring it costs nothing extra).
//
// Stale-ID tolerance: target IDs are only meaningful within the current
// discovery cache and can go stale across a restart/registry change. This
// store restores the raw ID list from localStorage unconditionally and does
// NOT depend on (or import) the discovery store — the two stores stay
// independent. It is the CONSUMING component (Dashboard/GridView/TabView,
// T046/T047) that joins `openTargetIds` against `useDiscovery().targets` and
// simply skips rendering a `TerminalCard` for any id that doesn't currently
// resolve to a real `SessionTarget`.

import { useSyncExternalStore } from "react";
import type { SessionTarget } from "../api/client";

export type LayoutMode = "grid" | "tabs" | "focused";

const STORAGE_KEY = "remo-web:workspace";

interface PersistedWorkspaceState {
  openTargetIds: string[];
  layoutMode: LayoutMode;
  focusedTargetId: string | null;
}

type WorkspaceState = PersistedWorkspaceState;

const DEFAULT_LAYOUT_MODE: LayoutMode = "grid";

function isLayoutMode(value: unknown): value is LayoutMode {
  return value === "grid" || value === "tabs" || value === "focused";
}

function loadPersisted(): PersistedWorkspaceState {
  const fallback: PersistedWorkspaceState = {
    openTargetIds: [],
    layoutMode: DEFAULT_LAYOUT_MODE,
    focusedTargetId: null,
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
    const candidate = parsed as Partial<PersistedWorkspaceState>;
    const openTargetIds = Array.isArray(candidate.openTargetIds)
      ? candidate.openTargetIds.filter((id): id is string => typeof id === "string")
      : [];
    const layoutMode = isLayoutMode(candidate.layoutMode) ? candidate.layoutMode : DEFAULT_LAYOUT_MODE;
    const focusedTargetId =
      typeof candidate.focusedTargetId === "string" ? candidate.focusedTargetId : null;
    return { openTargetIds, layoutMode, focusedTargetId };
  } catch (error) {
    // Malformed/corrupted localStorage entry — start fresh rather than
    // crashing the workspace on load.
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
      openTargetIds: state.openTargetIds,
      layoutMode: state.layoutMode,
      focusedTargetId: state.focusedTargetId,
    };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(toPersist));
  } catch (error) {
    // Storage full/unavailable (e.g. private browsing) — the workspace still
    // works in-memory for the session, just without persistence.
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

function openTarget(target: SessionTarget): void {
  const alreadyOpen = state.openTargetIds.includes(target.id);
  const openTargetIds = alreadyOpen ? state.openTargetIds : [...state.openTargetIds, target.id];
  setState({ openTargetIds, focusedTargetId: target.id });
}

function openMany(targets: SessionTarget[]): void {
  if (targets.length === 0) {
    return;
  }
  const openTargetIds = [...state.openTargetIds];
  for (const target of targets) {
    if (!openTargetIds.includes(target.id)) {
      openTargetIds.push(target.id);
    }
  }
  // Bulk-open focuses the first newly-opened target if nothing was focused
  // before, otherwise leaves the current focus alone so e.g. "open all on
  // instance" doesn't yank focus away from a terminal the user is already
  // looking at.
  const focusedTargetId = state.focusedTargetId ?? targets[0].id;
  setState({ openTargetIds, focusedTargetId });
}

function closeTarget(targetId: string): void {
  const index = state.openTargetIds.indexOf(targetId);
  if (index === -1) {
    return;
  }
  const openTargetIds = state.openTargetIds.filter((id) => id !== targetId);

  let focusedTargetId = state.focusedTargetId;
  if (focusedTargetId === targetId) {
    // Focus the next target, falling back to the previous one, falling back
    // to null when the closed target was the last one open.
    focusedTargetId = openTargetIds[index] ?? openTargetIds[index - 1] ?? null;
  }

  setState({ openTargetIds, focusedTargetId });
}

function setLayoutMode(mode: LayoutMode): void {
  setState({ layoutMode: mode });
}

function setFocused(targetId: string | null): void {
  setState({ focusedTargetId: targetId });
}

export interface UseWorkspaceResult {
  openTargetIds: string[];
  layoutMode: LayoutMode;
  focusedTargetId: string | null;
  openTarget: (target: SessionTarget) => void;
  openMany: (targets: SessionTarget[]) => void;
  closeTarget: (targetId: string) => void;
  setLayoutMode: (mode: LayoutMode) => void;
  setFocused: (targetId: string | null) => void;
}

/**
 * React hook exposing the workspace store (mirrors `useDiscovery()`). The
 * action functions are module-level (stable identity across renders), so
 * they're returned as-is with no `useCallback` wrapping needed.
 */
export function useWorkspace(): UseWorkspaceResult {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);

  return {
    openTargetIds: snapshot.openTargetIds,
    layoutMode: snapshot.layoutMode,
    focusedTargetId: snapshot.focusedTargetId,
    openTarget,
    openMany,
    closeTarget,
    setLayoutMode,
    setFocused,
  };
}
