// Discovery store (T029, US1).
//
// Small, dependency-free store built on React's built-in `useSyncExternalStore`
// (no Redux/Zustand). A single module-level store is shared by every
// `useDiscovery()` caller so there is only ever one polling interval, and every
// subscribed component re-renders from the same snapshot.
//
// "Incremental" (FR-035): the backend's GET /hosts and GET /sessions each
// return a full current snapshot (not a stream), so incremental rendering is
// achieved by polling on an interval and replacing state reactively on every
// poll — whichever instances have resolved by a given poll are shown, rather
// than blocking a spinner on the slowest instance. `refresh()` additionally
// triggers POST /discovery/refresh then polls GET /hosts + GET /sessions a
// few times with a short delay, so newly-completed per-instance results
// appear as they land rather than all at once at the end.

import { useCallback, useEffect, useSyncExternalStore } from "react";
import {
  ApiError,
  getHosts,
  getSessions,
  isAuthChallenge,
  refreshDiscovery as refreshDiscoveryRequest,
  type DiscoveryInstance,
  type SessionTarget,
} from "../api/client";

const DEFAULT_AUTO_REFRESH_INTERVAL_MS = 15_000;
const POST_REFRESH_POLL_COUNT = 4;
const POST_REFRESH_POLL_DELAY_MS = 1_500;

interface DiscoveryState {
  instances: DiscoveryInstance[];
  targets: SessionTarget[];
  isRefreshing: boolean;
  lastRefreshedAt: string | null;
}

let state: DiscoveryState = {
  instances: [],
  targets: [],
  isRefreshing: false,
  lastRefreshedAt: null,
};

const listeners = new Set<() => void>();

function setState(partial: Partial<DiscoveryState>): void {
  state = { ...state, ...partial };
  for (const listener of listeners) {
    listener();
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): DiscoveryState {
  return state;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Only one poll runs at a time; overlapping interval ticks / manual refreshes
// are no-ops while a poll is already in flight, so responses can never race
// each other and clobber newer state with a stale one.
/** Log a failed poll, EXCEPT a forward-auth challenge. Returning to a
 * backgrounded tab with a lapsed SSO session challenges every in-flight
 * request at once; the client is already re-authenticating via a top-level
 * reload, and shouting about it fills the console with alarming errors for a
 * condition that resolves itself. */
function logPollFailure(what: string, error: unknown): void {
  if (isAuthChallenge(error)) {
    return;
  }
  console.error(`discovery: ${what} failed`, error);
}

let pollInFlight = false;

async function pollOnce(): Promise<void> {
  if (pollInFlight) {
    return;
  }
  pollInFlight = true;
  try {
    // Hosts and sessions are fetched independently so a slow one never
    // blocks the other from updating the UI (FR-035).
    const hostsPoll = getHosts()
      .then((response) => {
        setState({ instances: response.instances, lastRefreshedAt: new Date().toISOString() });
      })
      .catch((error: unknown) => {
        // Keep the last-known instances rather than blanking the dashboard
        // on a transient poll failure.
        logPollFailure("GET /hosts", error);
      });

    const sessionsPoll = getSessions()
      .then((response) => {
        setState({ targets: response.targets });
      })
      .catch((error: unknown) => {
        logPollFailure("GET /sessions", error);
      });

    await Promise.all([hostsPoll, sessionsPoll]);
  } finally {
    pollInFlight = false;
  }
}

let autoRefreshHandle: ReturnType<typeof setInterval> | undefined;
let subscriberCount = 0;

function startAutoRefresh(intervalMs: number): void {
  subscriberCount += 1;
  if (autoRefreshHandle !== undefined) {
    return;
  }
  // First launch: the backend discovery cache is empty until a discovery run
  // happens (GET /hosts only READS the cache). Trigger a full refresh — POST
  // /discovery/refresh + follow-up polls — so the registry is discovered
  // automatically without the user having to click "Refresh".
  void manualRefresh();
  // Every tick must trigger a discovery run too, not just re-read the cache.
  // GET /hosts and GET /sessions are cache reads: a tick that only polls them
  // re-renders the SAME snapshot forever, so the whole rail freezes at whatever
  // page load found — ⚡ session state, git ahead/behind, newly-created
  // projects, instance status. The user sees a session they just started never
  // light up until they reload the page.
  //
  // The POST is TTL-gated (`force: false`), so it is the service's
  // DISCOVERY_CACHE_TTL_S — not this interval, and not the number of open
  // browser tabs — that decides how often discovery actually runs.
  autoRefreshHandle = setInterval(() => {
    void tick();
  }, intervalMs);
}

function stopAutoRefresh(): void {
  subscriberCount = Math.max(0, subscriberCount - 1);
  if (subscriberCount === 0 && autoRefreshHandle !== undefined) {
    clearInterval(autoRefreshHandle);
    autoRefreshHandle = undefined;
  }
}

/**
 * Triggers server-side re-discovery (POST /discovery/refresh) and then polls
 * GET /hosts + GET /sessions a few times with a short delay, so per-instance
 * results appear incrementally as they land rather than all at once once the
 * slowest instance finishes (FR-035).
 */
async function manualRefresh(instanceId?: string): Promise<void> {
  setState({ isRefreshing: true });
  try {
    try {
      await refreshDiscoveryRequest(instanceId);
    } catch (error) {
      // The refresh trigger failing (e.g. transient network error) shouldn't
      // stop us from polling — a prior/concurrent discovery cycle may still
      // land fresh data.
      if (!(error instanceof ApiError)) {
        throw error;
      }
      logPollFailure("POST /discovery/refresh", error);
    }

    for (let i = 0; i < POST_REFRESH_POLL_COUNT; i += 1) {
      await pollOnce();
      if (i < POST_REFRESH_POLL_COUNT - 1) {
        await sleep(POST_REFRESH_POLL_DELAY_MS);
      }
    }
  } finally {
    setState({ isRefreshing: false });
  }
}

/**
 * One background tick: ask the service for a TTL-gated discovery run, then read
 * the cache. Deliberately does NOT set `isRefreshing` — that flag drives the
 * "Refreshing…" affordance next to the user's own Refresh button, and a
 * background tick flickering it every interval would be noise.
 *
 * A failed POST is swallowed for the same reason `manualRefresh` swallows it:
 * the following GETs may still land useful data, and a transient blip on a
 * background tick is not worth surfacing.
 */
async function tick(): Promise<void> {
  try {
    await refreshDiscoveryRequest(undefined, { force: false });
  } catch (error) {
    if (!(error instanceof ApiError)) {
      throw error;
    }
    logPollFailure("background POST /discovery/refresh", error);
  }
  await pollOnce();
}

export interface UseDiscoveryResult {
  instances: DiscoveryInstance[];
  targets: SessionTarget[];
  refresh: (instanceId?: string) => Promise<void>;
  isRefreshing: boolean;
  lastRefreshedAt: string | null;
}

/**
 * React hook exposing the discovery store. Starts (and shares) an
 * interval-driven auto-refresh while mounted, plus a manual `refresh()`
 * trigger. Safe to call from multiple components at once — they all read
 * the same underlying snapshot and share a single polling interval.
 */
export function useDiscovery(
  autoRefreshIntervalMs: number = DEFAULT_AUTO_REFRESH_INTERVAL_MS,
): UseDiscoveryResult {
  const snapshot = useSyncExternalStore(subscribe, getSnapshot);

  useEffect(() => {
    startAutoRefresh(autoRefreshIntervalMs);
    return () => stopAutoRefresh();
  }, [autoRefreshIntervalMs]);

  const refresh = useCallback((instanceId?: string) => manualRefresh(instanceId), []);

  return {
    instances: snapshot.instances,
    targets: snapshot.targets,
    refresh,
    isRefreshing: snapshot.isRefreshing,
    lastRefreshedAt: snapshot.lastRefreshedAt,
  };
}
