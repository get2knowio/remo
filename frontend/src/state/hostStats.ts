// Live host-stats polling for the host detail page (plan §2.4).
//
// Unlike discovery/health this is NOT a shared module-level store: stats are
// per-instance and only wanted while that host's detail page is open, so the
// hook owns its own interval and state. The server coalesces multi-tab
// polling behind a per-instance TTL cache, so a second mounted hook costs at
// most one SSH call per TTL anyway.
//
// Behavior:
//   - poll every 5s while mounted;
//   - pause while the document is hidden, refetch immediately on visible;
//   - keep the last snapshot (flagged `stale`) when a poll fails, so the page
//     never blanks over a transient error;
//   - stop polling entirely on a 409 `unsupported_host_tools` envelope — the
//     host's tools predate the stats verb, and re-asking every 5s cannot
//     change that. The envelope (with its remediation text naming the exact
//     upgrade/configure command) is surfaced as `unsupported`.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getHostStats, type HostStats, type TypedError } from "../api/client";

const POLL_INTERVAL_MS = 5_000;

export interface UseHostStatsResult {
  /** Latest snapshot, or null before the first successful poll. */
  stats: HostStats | null;
  /** True when the most recent poll failed but an older snapshot is kept. */
  stale: boolean;
  /** The 409 `unsupported_host_tools` envelope, once seen. Polling stops. */
  unsupported: TypedError | null;
  /** Force an immediate refetch (e.g. the page's Refresh button). */
  refetch: () => Promise<void>;
}

export function useHostStats(instanceId: string): UseHostStatsResult {
  const [stats, setStats] = useState<HostStats | null>(null);
  const [stale, setStale] = useState(false);
  const [unsupported, setUnsupported] = useState<TypedError | null>(null);
  // Read by the interval/visibility handlers without re-arming them.
  const stoppedRef = useRef(false);
  const inFlightRef = useRef(false);

  const pollOnce = useCallback(async (): Promise<void> => {
    if (stoppedRef.current || inFlightRef.current) {
      return;
    }
    inFlightRef.current = true;
    try {
      const snapshot = await getHostStats(instanceId);
      setStats(snapshot);
      setStale(false);
    } catch (error) {
      if (error instanceof ApiError && error.code === "unsupported_host_tools") {
        stoppedRef.current = true;
        setUnsupported({
          code: error.code,
          message: error.message,
          retryable: error.retryable,
          remediation: error.remediation,
        });
        return;
      }
      // Transient failure: keep the last snapshot, badge it stale.
      setStale(true);
    } finally {
      inFlightRef.current = false;
    }
  }, [instanceId]);

  useEffect(() => {
    stoppedRef.current = false;
    setStats(null);
    setStale(false);
    setUnsupported(null);

    void pollOnce();
    const interval = setInterval(() => {
      // Paused while hidden — a backgrounded tab polling load/temps every 5s
      // is pure waste. The visibilitychange handler refetches on return.
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      void pollOnce();
    }, POLL_INTERVAL_MS);

    const onVisibility = (): void => {
      if (document.visibilityState === "visible") {
        void pollOnce();
      }
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }

    return () => {
      stoppedRef.current = true;
      clearInterval(interval);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
    };
  }, [pollOnce]);

  return { stats, stale, unsupported, refetch: pollOnce };
}
