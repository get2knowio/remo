// Health store (console redesign).
//
// Polls GET /api/v1/ready to drive the top-bar health indicator and the
// offline overlay. Same shared-interval, ref-counted `useSyncExternalStore`
// pattern as `discovery.ts`.
//
//   - "loading"       before the first poll returns.
//   - "healthy"       /ready returned 200 (all gating checks pass).
//   - "unconfigured"  /ready returned 200 with status "unconfigured" — the
//                     service is up but awaiting adoption (011-web-adopt).
//                     Drives the AwaitingAdoption page; polling continues so
//                     the app flips to the dashboard automatically once
//                     `remo web adopt` completes.
//   - "degraded"      /ready returned 503 (reachable, but a config check
//                     failed — e.g. missing SSH identity). `detail`/`checks`
//                     explain it.
//   - "offline"       the request failed at the network level (service down /
//                     restarting). Drives the offline overlay.
//
// A forward-auth challenge (expired SSO session behind an access proxy) is NOT
// any of these: the service is fine, the browser just needs to re-auth. The
// client turns it into a top-level reload and an `auth_challenge` error, which
// this store deliberately ignores so the offline overlay never flashes over a
// page that is one navigation away from working again.

import { useCallback, useEffect, useSyncExternalStore } from "react";
import {
  ApiError,
  getHealth,
  getReady,
  isAuthChallenge,
  type ReadinessResponse,
} from "../api/client";

const DEFAULT_POLL_INTERVAL_MS = 10_000;

export type HealthStatus = "loading" | "healthy" | "unconfigured" | "degraded" | "offline";

interface HealthState {
  status: HealthStatus;
  checks: Record<string, string>;
  detail: string | null;
  /** GET /health `features.host_admin` — whether the gated host-maintenance
   * API + host-shell terminal path are enabled. Defaults to false until the
   * flag is actually observed (absent field, old service, failed fetch), so
   * the console never renders mutating affordances it can't back. */
  hostAdmin: boolean;
  /** GET /health `features.registry_admin` (023) — whether the console may
   * add/remove/configure hosts. Reported as EFFECTIVE availability (flag on
   * AND not mount-configured); same false-until-observed default. */
  registryAdmin: boolean;
  /** GET /health `registry_change` (023) — the last registry change's
   * generation/time/origin, or null. `origin === "web"` drives the
   * unsynced-changes badge in Settings. Refreshed on every health poll (the
   * one field here that is state, not config). */
  registryChange: { generation: number; at: string; origin: string } | null;
}

let state: HealthState = {
  status: "loading",
  checks: {},
  detail: null,
  hostAdmin: false,
  registryAdmin: false,
  registryChange: null,
};

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

async function resolveFeatures(): Promise<void> {
  // Unlike the feature flags (config — stable for a page life), 023's
  // `registry_change` is state, so /health is fetched on every poll tick;
  // the flags just stop changing after the first successful read.
  try {
    const health = await getHealth();
    setState({
      hostAdmin: health.features?.host_admin === true,
      registryAdmin: health.features?.registry_admin === true,
      registryChange: health.registry_change ?? null,
    });
  } catch {
    // Leave the current values; the next poll tick retries.
  }
}

let pollInFlight = false;

async function pollOnce(): Promise<void> {
  if (pollInFlight) {
    return;
  }
  pollInFlight = true;
  void resolveFeatures();
  try {
    const ready: ReadinessResponse = await getReady();
    // Any 200 status other than "unconfigured" (e.g. "ok") means configured.
    const status: HealthStatus = ready.ready
      ? ready.status === "unconfigured"
        ? "unconfigured"
        : "healthy"
      : "degraded";
    setState({
      status,
      checks: ready.checks,
      detail: ready.detail ?? null,
    });
  } catch (error) {
    if (isAuthChallenge(error)) {
      // A re-auth navigation is under way (auth_challenge), or re-auth already
      // failed once and the client has stopped looping (auth_required). Either
      // way the service's own health is unknown, not bad — leave the last known
      // status alone rather than claiming an outage we haven't observed.
      return;
    }
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
  /** features.host_admin from GET /health; false until observed true. */
  hostAdmin: boolean;
  /** features.registry_admin from GET /health; false until observed true. */
  registryAdmin: boolean;
  /** registry_change from GET /health; null until observed. */
  registryChange: { generation: number; at: string; origin: string } | null;
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
    hostAdmin: snapshot.hostAdmin,
    registryAdmin: snapshot.registryAdmin,
    registryChange: snapshot.registryChange,
    retry,
  };
}
