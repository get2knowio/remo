// Read-only console diagnostics snapshot.
//
// Console-owned shapes (hand-written, not generated): this describes the
// BROWSER's state, which no service contract covers. The one generated type
// here is `HealthResponse`, read for the service version.
//
// Why this exists: every hard web-console question so far — a terminal grid
// that doesn't fit its box, selection dying under a TUI, a link that won't
// open, a pane that never resizes after a tab switch — turns on facts the app
// holds privately and exposed nowhere. The emulator is ref-private inside a
// TerminalCard, close codes were read and discarded, and the fit loop's
// last-sent grid is closure state. This module makes all of it readable in one
// JSON blob a user can paste into a bug report.
//
// Two entry points, deliberately:
//   - Settings -> "Copy diagnostics" (components/DiagnosticsSection.tsx), for
//     when the UI works.
//   - `window.__remo.diagnostics()` in devtools, for when it doesn't — which
//     is why `collectDiagnostics()` is SYNCHRONOUS and must never throw.
//
// REDACTION CONTRACT (pinned by a test in diagnostics.test.ts): this snapshot
// carries identifiers, state-machine fields, geometry numbers, renderer
// facts, a theme label and RTT. It carries NO terminal buffer or selection
// text, no `ws_token`, and no WebSocket `url`/`protocol` — the auth token
// rides as a WS subprotocol value, so those fields are excluded at the source
// (see terminal/TerminalConnection.ts's ConnectionDiagnostics).

import { getHealth } from "../api/client";
import { NARROW_BREAKPOINT } from "../lib/breakpoints";
import type { FitLoopSnapshot } from "../terminal/fitLoop";
import type { RendererDiagnostics, RendererRect } from "../terminal/RendererAdapter";
import type { ConnectionDiagnostics } from "../terminal/TerminalConnection";
import { getWorkspace } from "./workspace";

/** Everything one open terminal pane knows about itself. */
export interface PaneDiagnostics {
  id: string;
  target: { project: string; instanceType: string; instanceName: string };
  /** False for a pane kept mounted behind `display: none` — those entries are
   * the evidence for "it was the wrong size until I switched back to it". */
  visible: boolean;
  focused: boolean;
  connection: ConnectionDiagnostics;
  geometry: {
    /** The card surface's CURRENT box, measured fresh. Compare `bottom`
     * against `env.viewport.height` to tell "the box itself hangs below the
     * fold" from "the grid is sized for the wrong box" (which instead shows
     * up as `renderer.grid` !== `renderer.proposedGrid`). */
    containerPx: RendererRect | null;
    fitLoop: FitLoopSnapshot;
  };
  renderer: RendererDiagnostics;
  font: { family: string; size: number; ligatures: boolean };
  themeLabel: string;
  rttMs: number | null;
}

/** Stand-in for a pane whose provider threw. Keeping the id (and the error)
 * beats losing the whole snapshot to one broken card. */
export interface PaneDiagnosticsError {
  id: string;
  error: string;
}

export type PaneEntry = PaneDiagnostics | PaneDiagnosticsError;

export interface DiagnosticsSnapshot {
  generatedAt: string;
  /** `service` is null until `/health` has answered at least once — the
   * devtools path is synchronous by design and does not wait for it. */
  versions: { service: string | null };
  env: {
    userAgent: string;
    platform: string;
    devicePixelRatio: number;
    /** Pinch-zoom scale; null where `visualViewport` is unavailable. */
    visualViewportScale: number | null;
    viewport: { width: number; height: number };
    narrow: boolean;
  };
  layout: {
    kind: "grid" | "master";
    masterId: string | null;
    masterSide: string | null;
    /** The single-vs-grid axis, derived exactly as WorkspacePane does. A
     * different axis from `kind`, which is uniform-vs-tiled. */
    paneMode: "single" | "grid";
    focusedId: string | null;
    maximizedId: string | null;
    attached: number;
    visible: number;
  };
  panes: PaneEntry[];
}

const providers = new Map<string, () => PaneDiagnostics>();

/** A mounted TerminalCard offers its facts here for its lifetime. Set/delete
 * keyed by target id, so React StrictMode's double mount is idempotent. */
export function registerPaneDiagnostics(id: string, provider: () => PaneDiagnostics): void {
  providers.set(id, provider);
}

export function removePaneDiagnostics(id: string): void {
  providers.delete(id);
}

/** Cached across calls: the version cannot change without a page reload, and
 * the synchronous collector cannot await a fetch. */
let serviceVersion: string | null = null;

/**
 * Resolve (and cache) the service version. Only a SUCCESS is cached, so a call
 * made while the service was unreachable doesn't poison later ones. Never
 * rejects — an unknown version must not block copying a snapshot.
 */
export async function ensureServiceVersion(): Promise<string | null> {
  if (serviceVersion !== null) {
    return serviceVersion;
  }
  try {
    const health = await getHealth();
    serviceVersion = health.version || null;
  } catch {
    serviceVersion = null;
  }
  return serviceVersion;
}

function collectEnv(): DiagnosticsSnapshot["env"] {
  const width = typeof window === "undefined" ? 0 : window.innerWidth;
  return {
    userAgent: navigator.userAgent,
    // Deprecated but still the only cheap OS hint every browser reports, and
    // the macOS/other split is load-bearing for selection + keybinding bugs.
    platform: navigator.platform,
    devicePixelRatio: window.devicePixelRatio,
    // Absent in jsdom and in older browsers.
    visualViewportScale: window.visualViewport?.scale ?? null,
    viewport: { width, height: window.innerHeight },
    narrow: width < NARROW_BREAKPOINT,
  };
}

function collectLayout(): DiagnosticsSnapshot["layout"] {
  const ws = getWorkspace();
  const maximized =
    ws.maximizedId !== null && ws.attached.includes(ws.maximizedId) ? ws.maximizedId : null;
  return {
    kind: ws.layout.kind,
    masterId: ws.layout.kind === "master" ? ws.layout.id : null,
    masterSide: ws.layout.kind === "master" ? ws.layout.side : null,
    // Mirrors WorkspacePane's derivation; kept in step with it deliberately,
    // since a snapshot that disagrees with the render is worse than none.
    paneMode: maximized || ws.visible.length <= 1 ? "single" : "grid",
    focusedId: ws.focusedId,
    maximizedId: ws.maximizedId,
    attached: ws.attached.length,
    visible: ws.visible.length,
  };
}

/** Registered ids in the order WorkspacePane renders them: visible tiles in
 * grid order, then the hidden-but-attached ones. Anything registered that the
 * workspace no longer lists is appended rather than dropped. */
function orderedIds(): string[] {
  const ws = getWorkspace();
  const ordered = [...ws.visible, ...ws.attached.filter((id) => !ws.visible.includes(id))];
  const seen = new Set(ordered);
  return [
    ...ordered.filter((id) => providers.has(id)),
    ...[...providers.keys()].filter((id) => !seen.has(id)),
  ];
}

/**
 * Build the snapshot. SYNCHRONOUS and total: this is the escape hatch used
 * when the UI is broken, so a single misbehaving pane (or an environment
 * missing an API) degrades that one entry instead of throwing.
 */
export function collectDiagnostics(): DiagnosticsSnapshot {
  const panes: PaneEntry[] = orderedIds().map((id) => {
    try {
      return providers.get(id)!();
    } catch (error) {
      return { id, error: error instanceof Error ? error.message : String(error) };
    }
  });

  return {
    generatedAt: new Date().toISOString(),
    versions: { service: serviceVersion },
    env: collectEnv(),
    layout: collectLayout(),
    panes,
  };
}

declare global {
  interface Window {
    __remo?: { diagnostics(): DiagnosticsSnapshot };
  }
}

/**
 * Publish the devtools entry point. Called from main.tsx BEFORE mounting, so
 * `__remo.diagnostics()` exists even when the app never renders its shell (an
 * unconfigured service, a health gate, a crashed tree) — the registry is then
 * simply empty and the env/version halves still answer.
 */
export function installRemoGlobal(): void {
  if (typeof window === "undefined") {
    return;
  }
  window.__remo = { ...window.__remo, diagnostics: collectDiagnostics };
  // Fire-and-forget: fills in versions.service for later calls.
  void ensureServiceVersion();
}
