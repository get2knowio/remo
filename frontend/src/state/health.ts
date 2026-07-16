// Health store (console redesign).
//
// Polls GET /api/v1/ready to drive the top-bar health indicator and the
// offline overlay. Same shared-interval, ref-counted `useSyncExternalStore`
// pattern as `discovery.ts`.
//
//   - "loading"  before the first poll returns.
//   - "healthy"  /ready returned 200 (all gating checks pass).
//   - "degraded" /ready returned 503 (reachable, but a config check failed —
//                e.g. missing SSH identity). `detail`/`checks` explain it.
//   - "offline"  the request failed at the network level (service down /
//                restarting). Drives the offline overlay.

import { useCallback, useEffect, useSyncExternalStore } from "react";
import { ApiError, getReady, type ReadinessResponse } from "../api/client";

const DEFAULT_POLL_INTERVAL_MS = 10_000;

export type HealthStatus = "loading" | "healthy" | "degraded" | "offline";

interface HealthState {
  status: HealthStatus;
  checks: Record<string, string>;
  detail: string | null;
}

let state: HealthState = { status: "loading", checks: {}, detail: null };

const listeners = new Set<() => void>();

function setState(partial: Partial<HealthState>): void {
  state = { ...state, ...partial };
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): HealthState {
  return state;
}

let pollInFlight = false;

async function pollOnce(): Promise<void> {
  if (pollInFlight) {
    return;
  }
  pollInFlight = true;
  try {
    const ready: ReadinessResponse = await getReady();
    setState({
      status: ready.ready ? "healthy" : "degraded",
      checks: ready.checks,
      detail: ready.detail ?? null,
    });
  } catch (error) {
    if (error instanceof ApiError && error.code === "network_error") {
      setState({ status: "offline", detail: "The Remo web service is unreachable." });
    } else {
      // Unexpected shape — treat as degraded rather than offline.
      setState({ status: "degraded", detail: error instanceof Error ? error.message : null });
    }
  } finally {
    pollInFlight = false;
  }
}

let pollHandle: ReturnType<typeof setInterval> | undefined;
let subscriberCount = 0;

function startPolling(intervalMs: number): void {
  subscriberCount += 1;
  if (pollHandle !== undefined) {
    return;
  }
  void pollOnce();
  pollHandle = setInterval(() => void pollOnce(), intervalMs);
}

function stopPolling(): void {
  subscriberCount = Math.max(0, subscriberCount - 1);
  if (subscriberCount === 0 && pollHandle !== undefined) {
    clearInterval(pollHandle);
    pollHandle = undefined;
  }
}

export interface UseHealthResult {
  status: HealthStatus;
  checks: Record<string, string>;
  detail: string | null;
  /** Force an immediate re-poll (offline overlay "Retry connection"). */
  retry: () => Promise<void>;
}

export function useHealth(intervalMs: number = DEFAULT_POLL_INTERVAL_MS): UseHealthResult {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);
  useEffect(() => {
    startPolling(intervalMs);
    return () => stopPolling();
  }, [intervalMs]);
  const retry = useCallback(() => pollOnce(), []);
  return {
    status: snapshot.status,
    checks: snapshot.checks,
    detail: snapshot.detail,
    retry,
  };
}
