// WebSocket latency store (header indicator).
//
// Each connected terminal measures its WS round-trip (ping→pong in
// TerminalConnection) and reports it here keyed by session-target id. The header
// shows the MEDIAN across open terminals — since every terminal's WebSocket
// terminates at the same remo-web service, they all measure the same
// browser↔service path, so the median is a robust single number. Terminals
// remove their sample when they disconnect/unmount, so the store only ever holds
// currently-healthy connections (no freshness timer needed).
//
// The cached-snapshot pattern keeps `getSnapshot()` referentially stable between
// mutations (returning a fresh object each call would loop useSyncExternalStore).

import { useSyncExternalStore } from "react";

const samples = new Map<string, number>();
const listeners = new Set<() => void>();

export interface LatencySnapshot {
  /** Median WS round-trip in ms across open terminals, or null if none. */
  rttMs: number | null;
  /** How many terminals are currently reporting. */
  count: number;
}

let snapshot: LatencySnapshot = { rttMs: null, count: 0 };

function recompute(): void {
  if (samples.size === 0) {
    snapshot = { rttMs: null, count: 0 };
    return;
  }
  const sorted = [...samples.values()].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  snapshot = { rttMs: median, count: sorted.length };
}

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function reportLatency(id: string, rttMs: number): void {
  samples.set(id, rttMs);
  recompute();
  emit();
}

export function removeLatency(id: string): void {
  if (samples.delete(id)) {
    recompute();
    emit();
  }
}

/** Test/inspection accessor for the current aggregate. */
export function getLatencySnapshot(): LatencySnapshot {
  return snapshot;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useLatency(): LatencySnapshot {
  return useSyncExternalStore(subscribe, getLatencySnapshot, getLatencySnapshot);
}
